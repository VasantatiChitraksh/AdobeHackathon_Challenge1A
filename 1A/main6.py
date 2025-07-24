import fitz  # PyMuPDF
import json
import re
import os
from collections import defaultdict

def extract_headings_from_pdf(pdf_path):
    """
    Extracts a structured outline from a PDF using a multi-pass strategy.

    1.  **Rule-Based Pass:** Identifies numbered headings (e.g., "1.1 Section").
    2.  **Font-Based Pass:** Identifies headings based on font size and boldness.
    3.  **Filtering Pass:** Cleans the results, removing duplicates, headers, footers,
        and table of contents entries.

    Args:
        pdf_path (str): The file path to the PDF.

    Returns:
        dict: A dictionary containing the document title and a list of headings.
    """
    doc = fitz.open(pdf_path)
    headings = []
    
    # --- Pass 1: Rule-Based Numbered Heading Extraction ---
    # This pattern is highly reliable and should be checked first.
    numbered_heading_pattern = re.compile(r'^\s*(\d+(\.\d+)*)\s+([A-Z].*)$')
    for page_num, page in enumerate(doc):
        blocks = page.get_text("blocks")
        for b in blocks:
            text = b[4] # The text content of the block
            match = numbered_heading_pattern.match(text)
            if match:
                level_depth = match.group(1).count('.') + 1
                level = f"H{min(level_depth, 6)}" # Cap at H6
                headings.append({
                    "level": level,
                    "text": text.strip(),
                    "page": page_num,
                    "style": "numbered" # Mark to avoid re-processing
                })

    # --- Pass 2: Font-Based Heading Extraction ---
    spans = []
    for page_num, page in enumerate(doc):
        page_height = page.rect.height
        blocks = page.get_text("dict").get("blocks", [])
        for block in blocks:
            # Positional Filtering: Exclude top 10% (headers) and bottom 10% (footers)
            if block['type'] == 0 and block['bbox'][1] > page_height * 0.1 and block['bbox'][3] < page_height * 0.9:
                for line in block.get("lines", []):
                    # TOC Filtering: Skip lines that look like "Section ..... 123"
                    line_text = "".join(s['text'] for s in line.get("spans", []))
                    if re.search(r'\s*\.{5,}\s*\d+$', line_text):
                        continue
                        
                    for span in line.get("spans", []):
                        text = span["text"].strip()
                        if text:
                            spans.append({
                                "text": text,
                                "size": round(span["size"], 2),
                                "font": span["font"],
                                "bold": "bold" in span["font"].lower(),
                                "page": page_num
                            })

    if spans:
        font_styles = defaultdict(int)
        for span in spans:
            font_styles[(span["size"], span["bold"])] += len(span["text"])
        
        # Identify body text style not just by frequency, but by being common and not bold
        sorted_styles = sorted(font_styles.items(), key=lambda item: item[1], reverse=True)
        body_size = sorted_styles[0][0][0] if sorted_styles else 12.0 # Default fallback
        for style, count in sorted_styles:
            if not style[1]: # Prefer non-bold style as body text
                body_size = style[0]
                break
        
        # Any style larger than body text, or same size but bold, is a potential heading
        potential_heading_styles = [
            style for style in font_styles
            if style[0] > body_size or (style[0] == body_size and style[1])
        ]
        potential_heading_styles.sort(key=lambda x: (x[0], x[1]), reverse=True)

        heading_levels = {}
        # Assign H1, H2, H3 to top 3 distinct font sizes
        distinct_sizes = sorted(list(set(s[0] for s in potential_heading_styles)), reverse=True)
        if len(distinct_sizes) > 0: heading_levels['H1'] = distinct_sizes[0]
        if len(distinct_sizes) > 1: heading_levels['H2'] = distinct_sizes[1]
        if len(distinct_sizes) > 2: heading_levels['H3'] = distinct_sizes[2]
        size_to_level = {size: level for level, size in heading_levels.items()}
        
        for span in spans:
            if span["size"] in size_to_level:
                headings.append({
                    "level": size_to_level[span["size"]],
                    "text": span["text"],
                    "page": span["page"],
                    "style": "font"
                })

    # --- Pass 3: Filtering and Deduplication ---
    final_headings = []
    seen = set()
    headings.sort(key=lambda x: (x['page'], x.get('bbox', [0,0])[1])) # Sort by page and position
    
    # Combine headings that were split into multiple spans
    temp_headings = []
    if headings:
        current_heading = headings[0]
        for i in range(1, len(headings)):
            next_heading = headings[i]
            # If same level, page, and style, and likely on same line, merge them
            if (next_heading['level'] == current_heading['level'] and
                next_heading['page'] == current_heading['page'] and
                next_heading['style'] == current_heading['style']):
                current_heading['text'] += " " + next_heading['text']
            else:
                temp_headings.append(current_heading)
                current_heading = next_heading
        temp_headings.append(current_heading)

    for h in temp_headings:
        text = h['text'].strip()
        # Final cleanup: no short/numeric/form-like text, and no duplicates.
        if len(text) > 2 and not text.isnumeric() and not text.endswith(':') and text.lower() not in seen:
            final_headings.append({
                "level": h['level'],
                "text": text,
                "page": h['page']
            })
            seen.add(text.lower())
            
    # --- Title Extraction ---
    title = os.path.basename(pdf_path).replace('.pdf', '') # Fallback
    h1_headings = [h['text'] for h in final_headings if h['level'] == 'H1']
    if h1_headings:
        title = min(h1_headings, key=len) # Prefer shorter H1s for the title
    else:
        # Fallback to largest text on the first page if no H1
        first_page_spans = sorted(
            [s for s in spans if s['page'] == 0], 
            key=lambda x: x['size'], 
            reverse=True
        )
        if first_page_spans:
            title = first_page_spans[0]['text']

    return {"title": title, "outline": final_headings}


def main():
    """
    Main function to process PDF files from an 'input' directory
    and save the JSON output to an 'output' directory.
    """
    input_dir = "input"
    output_dir = "output"
    os.makedirs(output_dir, exist_ok=True)

    if not os.path.exists(input_dir):
        print(f"Error: Input directory '{input_dir}' not found.")
        return

    pdf_files = [f for f in os.listdir(input_dir) if f.lower().endswith('.pdf')]
    if not pdf_files:
        print(f"No PDF files found in '{input_dir}'.")
        return

    for filename in pdf_files:
        pdf_path = os.path.join(input_dir, filename)
        output_path = os.path.join(output_dir, filename.replace('.pdf', '.json'))
        
        print(f"Processing '{filename}'...")
        try:
            result = extract_headings_from_pdf(pdf_path)
            with open(output_path, 'w', encoding='utf-8') as f:
                json.dump(result, f, indent=4, ensure_ascii=False)
            print(f"  -> Successfully extracted {len(result['outline'])} headings to '{output_path}'")
        except Exception as e:
            print(f"  -> Error processing '{filename}': {e}")

if __name__ == "__main__":
    main()