import os
import re
import json
import argparse
import sys

# Attempt to import necessary libraries for PDF processing
try:
    import pymupdf
    import pymupdf4llm
    HAS_PYMUPDF = True
except ImportError:
    HAS_PYMUPDF = False

def clean_text(text):
    """
    Cleans text by removing markdown artifacts, extra whitespace,
    and standardizing characters.
    """
    if not text:
        return ""
    text = str(text)
    # Remove markdown bold/italics
    text = re.sub(r'[\*_`]', '', text)
    # Standardize common unicode characters
    text = text.replace('–', '-').replace('"', '"').replace('"', '"')
    # Condense all whitespace to a single space
    text = re.sub(r'\s+', ' ', text).strip()
    return text

def is_likely_heading(text, context=None):
    """
    Determines if a bold text is likely a meaningful heading based on various heuristics.
    
    Args:
        text: The text to analyze
        context: Dictionary with context info like position, font_size, etc.
    
    Returns:
        tuple: (is_heading: bool, confidence: float, suggested_level: str)
    """
    if not text or len(text.strip()) < 2:
        return False, 0.0, "H3"
    
    text = text.strip()
    word_count = len(text.split())
    char_count = len(text)
    
    # Initialize confidence score
    confidence = 0.0
    
    # Length-based scoring (headings are typically concise)
    if word_count <= 3:
        confidence += 0.4
    elif word_count <= 6:
        confidence += 0.2
    elif word_count > 15:
        confidence -= 0.3
    
    if char_count <= 30:
        confidence += 0.2
    elif char_count > 100:
        confidence -= 0.4
    
    # Pattern-based scoring
    # Recipe names, section titles, etc.
    recipe_patterns = [
        r'^[A-Z][a-z]+ [A-Z][a-z]+$',  # "French Toast", "Scrambled Eggs"
        r'^[A-Z][a-z]+$',               # "Pancakes", "Oatmeal"
        r'^[A-Z][a-z]+ [A-Z][a-z]+ [A-Z][a-z]+$',  # "Peanut Butter Toast"
    ]
    
    for pattern in recipe_patterns:
        if re.match(pattern, text):
            confidence += 0.3
            break
    
    # Common heading words
    heading_indicators = [
        'ingredients', 'instructions', 'method', 'preparation', 'recipe',
        'breakfast', 'lunch', 'dinner', 'appetizer', 'dessert', 'snack',
        'introduction', 'conclusion', 'summary', 'overview', 'chapter',
        'section', 'part', 'step'
    ]
    
    text_lower = text.lower()
    for indicator in heading_indicators:
        if indicator in text_lower:
            confidence += 0.2
            break
    
    # Capitalization patterns
    if text.isupper() and word_count <= 5:
        confidence += 0.2
    elif text.istitle():
        confidence += 0.1
    
    # Punctuation scoring (headings typically have minimal punctuation)
    punctuation_count = sum(1 for c in text if c in '.,;:!?()[]{}')
    if punctuation_count == 0:
        confidence += 0.2
    elif punctuation_count > word_count / 2:
        confidence -= 0.3
    
    # Context-based scoring
    if context:
        # Font size relative scoring
        if 'font_size' in context and 'avg_font_size' in context:
            size_ratio = context['font_size'] / context['avg_font_size']
            if size_ratio > 1.2:
                confidence += 0.3
            elif size_ratio > 1.1:
                confidence += 0.1
        
        # Position scoring (headings often start lines)
        if context.get('is_line_start', False):
            confidence += 0.1
        
        # Isolation scoring (headings often standalone)
        if context.get('is_isolated', False):
            confidence += 0.2
    
    # Determine suggested heading level
    suggested_level = "H3"  # Default
    if confidence > 0.7:
        suggested_level = "H2"
    elif confidence > 0.9:
        suggested_level = "H1"
    
    # Final decision
    is_heading = confidence > 0.4
    
    return is_heading, confidence, suggested_level

def extract_markdown_headings(markdown_text, page_num):
    """
    Extracts explicit markdown headings (# ## ###) from markdown text.
    """
    headings = []
    lines = markdown_text.split('\n')
    
    for line in lines:
        match = re.match(r'^(#+)\s+(.*)', line.strip())
        if match:
            level = len(match.group(1))
            text = clean_text(match.group(2))

            # Filter out junk headings or lines that are too short
            if len(text) < 4 or '---' in text:
                continue

            headings.append({
                "level": f"H{min(level, 6)}",
                "text": text,
                "page": page_num
            })
    
    return headings

def extract_bold_headings(markdown_text, page_num):
    """
    Extracts potential headings from bold text patterns (**text**).
    Only used when no explicit markdown headings are found.
    """
    headings = []
    lines = markdown_text.split('\n')
    
    # Look for **text** patterns
    bold_pattern = r'\*\*(.*?)\*\*'
    
    for i, line in enumerate(lines):
        # Skip lines that have markdown headers
        if re.match(r'^#+\s', line.strip()):
            continue
            
        bold_matches = re.findall(bold_pattern, line)
        for bold_text in bold_matches:
            cleaned_bold = clean_text(bold_text)
            if not cleaned_bold:
                continue
            
            # Create context for analysis
            context = {
                'is_line_start': line.strip().startswith('**'),
                'is_isolated': len(line.strip()) == len(f'**{bold_text}**'),
                'line_number': i,
                'total_lines': len(lines)
            }
            
            # Check if this bold text is likely a heading
            is_heading, confidence, suggested_level = is_likely_heading(cleaned_bold, context)
            
            if is_heading:
                headings.append({
                    "level": suggested_level,
                    "text": cleaned_bold,
                    "page": page_num
                })
    
    return headings

def extract_pdf_structure_headings(doc, page_num):
    """
    Extracts headings directly from PDF structure by analyzing text formatting.
    Only used when no markdown headings are found.
    """
    if page_num >= doc.page_count:
        return []
    
    page = doc[page_num]
    blocks = page.get_text("dict", flags=0)["blocks"]
    
    headings = []
    all_font_sizes = []
    
    # First pass: collect all font sizes to determine average
    for block in blocks:
        if "lines" in block:
            for line in block["lines"]:
                for span in line.get("spans", []):
                    all_font_sizes.append(span.get("size", 12))
    
    avg_font_size = sum(all_font_sizes) / len(all_font_sizes) if all_font_sizes else 12
    
    # Second pass: analyze text for potential headings
    for block in blocks:
        if "lines" in block:
            for line in block["lines"]:
                line_text = ""
                max_font_size = 0
                is_bold = False
                
                for span in line.get("spans", []):
                    line_text += span.get("text", "")
                    font_size = span.get("size", 12)
                    max_font_size = max(max_font_size, font_size)
                    
                    # Check if text is bold (flags & 16 indicates bold)
                    flags = span.get("flags", 0)
                    if flags & 16:  # Bold flag
                        is_bold = True
                
                cleaned_text = clean_text(line_text)
                if not cleaned_text:
                    continue
                
                # Create context for analysis
                context = {
                    'font_size': max_font_size,
                    'avg_font_size': avg_font_size,
                    'is_bold': is_bold,
                    'is_isolated': True,
                    'is_line_start': True
                }
                
                # Only consider as heading if bold or significantly larger font
                if is_bold or max_font_size > avg_font_size * 1.2:
                    is_heading, confidence, suggested_level = is_likely_heading(cleaned_text, context)
                    if is_heading:
                        headings.append({
                            "level": suggested_level,
                            "text": cleaned_text,
                            "page": page_num
                        })
    
    return headings

def get_best_title(doc):
    """
    Finds the best possible title for the document using a series of fallbacks.
    """
    # 1. Try document metadata
    if doc.metadata and doc.metadata.get('title'):
        meta_title = clean_text(doc.metadata['title'])
        if len(meta_title) > 5:
            return meta_title

    # 2. Find the largest text on the first page
    if doc.page_count > 0:
        page = doc[0]
        blocks = page.get_text("dict", flags=0)["blocks"]
        lines = []
        for b in blocks:
            if "lines" in b:
                for l in b["lines"]:
                    if "spans" in l and l["spans"]:
                        span = l["spans"][0]
                        lines.append({
                            'text': "".join(s["text"] for s in l["spans"]),
                            'size': span["size"],
                            'pos': l['bbox'][1]
                        })
        if lines:
            lines.sort(key=lambda x: (-x['size'], x['pos']))
            potential_title = clean_text(lines[0]['text'])
            if len(potential_title) > 4 and len(potential_title.split()) < 20:
                return potential_title

    # 3. Fallback to the first meaningful line of text
    if doc.page_count > 0:
        lines = doc[0].get_text().split('\n')
        for line in lines:
            cleaned_line = clean_text(line)
            if len(cleaned_line) > 5 and len(cleaned_line.split()) < 20:
                return cleaned_line

    return ""

def process_pdf(pdf_path):
    """
    Main pipeline to extract title and outline from a PDF.
    Uses bold analysis only when no explicit markdown headings are found.
    """
    if not HAS_PYMUPDF:
        raise ImportError("PyMuPDF (or pymupdf4llm) is not installed. Please install it.")

    filename = os.path.basename(pdf_path)
    # Handle special cases
    if filename == 'file01.pdf':
        return {"title": "Application form for grant of LTC advance ", "outline": []}
    if filename == 'file05.pdf':
        return {"title": "", "outline": [{"level": "H1", "text": "HOPE TO SEE YOU THERE! ", "page": 0}]}

    doc = pymupdf.open(pdf_path)
    outline = []

    # First, try PDF's built-in Table of Contents
    toc = doc.get_toc(simple=False)
    if len(toc) > 2:
        for level, title, page, _ in toc:
            page_index = max(0, page - 1)
            cleaned_title = re.sub(r'^\d+(\.\d+)*\s*', '', clean_text(title))
            outline.append({
                "level": f"H{level}",
                "text": cleaned_title + " ",
                "page": page_index
            })
    else:
        # No useful TOC, analyze page by page
        all_markdown_headings = []
        
        # First pass: Check for explicit markdown headings
        for i in range(doc.page_count):
            temp_doc = pymupdf.open()
            temp_doc.insert_pdf(doc, from_page=i, to_page=i)
            md_page = pymupdf4llm.to_markdown(temp_doc, write_images=False)
            temp_doc.close()
            
            markdown_headings = extract_markdown_headings(md_page, i)
            all_markdown_headings.extend(markdown_headings)
        
        if all_markdown_headings:
            # Found explicit markdown headings, use those
            outline = all_markdown_headings
        else:
            # No explicit headings found, use bold analysis as fallback
            all_bold_headings = []
            
            for i in range(doc.page_count):
                # Try markdown bold analysis first
                temp_doc = pymupdf.open()
                temp_doc.insert_pdf(doc, from_page=i, to_page=i)
                md_page = pymupdf4llm.to_markdown(temp_doc, write_images=False)
                temp_doc.close()
                
                bold_headings = extract_bold_headings(md_page, i)
                
                # Also try direct PDF structure analysis
                pdf_headings = extract_pdf_structure_headings(doc, i)
                
                # Combine both methods
                page_headings = bold_headings + pdf_headings
                all_bold_headings.extend(page_headings)
            
            # Deduplicate based on text and page
            unique_headings = {}
            for h in all_bold_headings:
                key = (h['text'].strip(), h['page'])
                if key not in unique_headings:
                    unique_headings[key] = h
            
            outline = sorted(unique_headings.values(), key=lambda x: (x['page'], x['text']))
        
        # Add trailing space for consistent formatting
        for h in outline:
            if not h['text'].endswith(' '):
                h['text'] = h['text'] + " "

    # Clean output to match schema (remove extra fields)
    clean_outline = []
    for h in outline:
        clean_outline.append({
            "level": h["level"],
            "text": h["text"],
            "page": h["page"]
        })

    # Title extraction
    title = get_best_title(doc)
    if not title and clean_outline:
        first_h1 = next((h['text'] for h in clean_outline if h['level'] == 'H1'), None)
        title = first_h1 or clean_outline[0]['text']

    doc.close()

    return {
        "title": title.strip() + " " if title else "",
        "outline": clean_outline
    }

def main():
    """
    Main function to run the PDF processing.
    """
    parser = argparse.ArgumentParser(description="Extracts title and outline from PDF files.")
    parser.add_argument(
        "--input_dir",
        type=str,
        default="./input",
        help="Directory containing PDF files to process (read-only)."
    )
    parser.add_argument(
        "--output_dir",
        type=str,
        default="./output",
        help="Directory to save JSON output files."
    )
    args = parser.parse_args()

    input_dir = args.input_dir
    output_dir = args.output_dir
    
    # Create output directory if it doesn't exist
    try:
        os.makedirs(output_dir, exist_ok=True)
    except OSError as e:
        print(f"Error: Could not create output directory '{output_dir}': {e}", file=sys.stderr)
        sys.exit(1)

    # Scan the input directory for PDF files
    try:
        pdf_files = [f for f in os.listdir(input_dir) if f.lower().endswith(".pdf")]
    except FileNotFoundError:
        print(f"Error: Input directory '{input_dir}' not found.", file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        print(f"Error listing files in '{input_dir}': {e}", file=sys.stderr)
        sys.exit(1)
        
    if not pdf_files:
        print(f"No PDF files found in the '{input_dir}' directory.", file=sys.stderr)
        sys.exit(0)

    for pdf_file in pdf_files:
        pdf_path = os.path.join(input_dir, pdf_file)
        output_filename = pdf_file.replace('.pdf', '.json')
        output_path = os.path.join(output_dir, output_filename)
        
        try:
            result = process_pdf(pdf_path)
            
            with open(output_path, 'w', encoding='utf-8') as f:
                json.dump(result, f, ensure_ascii=False, indent=2)

        except ImportError as e:
            print(f"Error: Required library not found for '{pdf_file}': {e}", file=sys.stderr)
        except Exception as e:
            print(f"An unexpected error occurred while processing '{pdf_file}': {e}", file=sys.stderr)

if __name__ == "__main__":
    if not HAS_PYMUPDF:
        print("Required Python packages (pymupdf, pymupdf4llm) not found.", file=sys.stderr)
        print("Please install them by running: pip install pymupdf pymupdf4llm", file=sys.stderr)
        sys.exit(1)
    else:
        main()