import fitz  # PyMuPDF
import json
import os
import re
from collections import defaultdict

# --- MODIFIED: Simplified text preprocessing ---
# The original multilingual preprocessing was overly complex and not central to the main issue.
# A simple normalization and space cleanup is sufficient for structure detection.
def preprocess_text(text):
    """Normalize whitespace and clean up text."""
    return re.sub(r'\s+', ' ', text).strip()

# --- MODIFIED: More robust heading characteristic detection ---
def is_potential_heading(span, page_height):
    """
    Checks if a text span has the properties of a heading.
    This is more conservative than the original function to reduce false positives.
    """
    text = span["text"]
    bbox = span["bbox"]
    
    # 1. Filter out common non-headings
    # Reject if it looks like a list item (handles bullet points from file02.pdf)
    if re.match(r'^\s*([•●▪▫■◆❖]|\*|-|\d+\.|\w\))\s+', text):
        return False
        
    # Reject if it's too long to be a heading
    if len(text.split()) > 15:
        return False

    # Reject if it's likely a header or footer based on position
    # Headers are in the top 10% of the page, footers in the bottom 10%
    if bbox[1] < page_height * 0.1 or bbox[3] > page_height * 0.9:
        return False

    # 2. Check for positive heading indicators
    # Font size is a strong indicator. Base size is ~10-12pt.
    is_large = span["size"] > 13.5
    
    # Bold is a strong indicator, especially when combined with size.
    is_bold = "bold" in span["font"].lower() or (span["flags"] & 2**4)
    
    # All caps is a good indicator, but only for short phrases.
    is_all_caps = text.isupper() and len(text.split()) < 7

    # A line ending with a colon is often a heading.
    ends_with_colon = text.endswith(':')

    # A potential heading must be large-ish and/or bold.
    if not (is_large or is_bold):
        return False

    return True

# --- NEW: Improved title extraction logic ---
def extract_title(doc, spans):
    """
    Extracts the document title using metadata and heuristics.
    Prioritizes the largest text on the first page.
    """
    # 1. Try PDF metadata first
    if doc.metadata and doc.metadata.get('title'):
        title = doc.metadata['title'].strip()
        if len(title) > 5:
            return title

    # 2. Find the most prominent text on the first page
    first_page_spans = [s for s in spans if s["page"] == 1]
    if not first_page_spans:
        # Fallback to filename if no text is found
        return os.path.splitext(os.path.basename(doc.name))[0].replace('_', ' ').title()

    # Sort spans by font size (desc), then by vertical position (asc)
    first_page_spans.sort(key=lambda s: (-s["size"], s["bbox"][1]))
    
    # The top, largest text is likely the title.
    potential_title = first_page_spans[0]["text"]
    if len(potential_title.split()) < 15: # A title shouldn't be a long paragraph
         return potential_title

    # 3. Fallback to the first H1 heading (will be determined later)
    return None # Will be replaced by the first H1 if found

class PDFOutlineExtractor:
    def extract(self, pdf_path):
        """Main method to extract title and outline from a PDF."""
        try:
            doc = fitz.open(pdf_path)
        except Exception as e:
            print(f"Error opening {pdf_path}: {e}")
            return None

        if doc.page_count == 0:
            return {"title": os.path.splitext(os.path.basename(pdf_path))[0], "outline": []}

        all_spans = []
        for page_num, page in enumerate(doc):
            page_height = page.rect.height
            blocks = page.get_text("dict")["blocks"]
            for block in blocks:
                if "lines" in block:
                    for line in block["lines"]:
                        for span in line["spans"]:
                            if span["text"].strip():
                                all_spans.append({
                                    "text": preprocess_text(span["text"]),
                                    "size": span["size"],
                                    "font": span["font"],
                                    "flags": span["flags"],
                                    "page": page_num + 1,
                                    "bbox": span["bbox"]
                                })
        
        # --- MODIFIED: More dynamic and robust heading classification ---
        page_height = doc[0].rect.height # Use first page height as a representative
        potential_headings = [s for s in all_spans if is_potential_heading(s, page_height)]
        
        if not potential_headings:
            title = extract_title(doc, all_spans)
            return {"title": title, "outline": []}

        # Group headings by font style (size and bold status)
        font_styles = defaultdict(list)
        for h in potential_headings:
            # Round size to group similar sizes (e.g., 15.9 and 16.0)
            style_key = (round(h['size']), "bold" in h['font'].lower() or (h["flags"] & 2**4))
            font_styles[style_key].append(h)
            
        # Sort font styles by size (desc) and boldness (bold first)
        # This determines the hierarchy of heading levels
        sorted_styles = sorted(font_styles.keys(), key=lambda k: (-k[0], -k[1]))
        
        # Map sorted styles to H1, H2, H3
        level_map = {}
        if len(sorted_styles) > 0: level_map[sorted_styles[0]] = "H1"
        if len(sorted_styles) > 1: level_map[sorted_styles[1]] = "H2"
        if len(sorted_styles) > 2:
            for style in sorted_styles[2:]:
                level_map[style] = "H3" # All other heading styles become H3

        # Create final outline
        outline = []
        for style, headings in font_styles.items():
            level = level_map.get(style)
            if level:
                for h in headings:
                    outline.append({"level": level, "text": h["text"], "page": h["page"]})
        
        # Sort final outline by page and position
        outline.sort(key=lambda x: (x["page"], [h for h in all_spans if h['text']==x['text'] and h['page']==x['page']][0]['bbox'][1]))
        
        # --- Final title determination ---
        title = extract_title(doc, all_spans)
        if not title:
            h1_headings = [h['text'] for h in outline if h['level'] == 'H1']
            if h1_headings:
                title = h1_headings[0]
            elif outline:
                title = outline[0]['text'] # Fallback to first detected heading
            else:
                title = os.path.splitext(os.path.basename(pdf_path))[0]

        doc.close()
        return {"title": title, "outline": outline}

def main():
    input_dir = "./input"
    output_dir = "./output"
    os.makedirs(output_dir, exist_ok=True)
    
    parser = PDFOutlineExtractor()

    pdf_files = [f for f in os.listdir(input_dir) if f.endswith('.pdf')]
    print(f"Found {len(pdf_files)} PDF file(s) to process.")

    for filename in pdf_files:
        pdf_path = os.path.join(input_dir, filename)
        output_path = os.path.join(output_dir, filename.replace('.pdf', '.json'))
        
        print(f"Processing: {filename}")
        try:
            result = parser.extract(pdf_path)
            if result:
                with open(output_path, 'w', encoding='utf-8') as f:
                    json.dump(result, f, ensure_ascii=False, indent=2)
                print(f"  ✓ Success -> {os.path.basename(output_path)}")
                print(f"    Title: {result['title']}")
                print(f"    Headings found: {len(result['outline'])}")
            else:
                print(f"  ✗ Failed to process {filename}")
        except Exception as e:
            print(f"  ✗ An unexpected error occurred with {filename}: {e}")

if __name__ == "__main__":
    main()