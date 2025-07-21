import unicodedata
import re
import fitz  # PyMuPDF
import json
import sys
import os
from collections import defaultdict


def preprocess_multilingual_text(text):
    """Clean and normalize multilingual text"""
    # Normalize Unicode (handles Japanese, Chinese, etc.)
    text = unicodedata.normalize('NFKC', text)

    # Remove excessive whitespace while preserving structure
    text = re.sub(r'\s+', ' ', text.strip())

    # Handle right-to-left languages (Arabic, Hebrew)
    if any(unicodedata.bidirectional(c) in ('R', 'AL') for c in text):
        # Apply RTL-specific processing if needed
        pass

    return text


def is_page_number(text):
    """Check if text is likely a page number"""
    text = text.strip()
    if not text:
        return False

    # Simple page number patterns
    if re.match(r'^\d+$', text) and len(text) <= 4:
        return True
    if re.match(r'^page\s+\d+$', text.lower()):
        return True
    if re.match(r'^\d+\s*of\s*\d+$', text.lower()):
        return True

    return False


def is_footer_header(text):
    """Check if text is likely a footer or header"""
    text = text.strip().lower()
    if not text:
        return False

    # Common footer/header patterns
    footer_patterns = [
        r'^copyright',
        r'^\d{4}.*all rights reserved',
        r'^confidential',
        r'^proprietary',
        r'^draft',
        r'^version\s+\d+',
        r'^\w+\.\w+',  # email-like
        r'^www\.',     # website
        r'^http',      # URL
    ]

    for pattern in footer_patterns:
        if re.match(pattern, text):
            return True

    return False


def has_heading_characteristics(span):
    """Check if span has heading characteristics"""
    text = span["text"]

    # Check for heading patterns (works across languages)
    heading_patterns = [
        r'^\d+\.?\s+',  # "1. " or "1 "
        # Uppercase (Latin, CJK, Hiragana, Katakana)
        r'^[A-Z\u00C0-\u017F\u4E00-\u9FFF\u3040-\u309F\u30A0-\u30FF]{2,}',
        r'^\w+\s*:',  # "Introduction:"
    ]

    for pattern in heading_patterns:
        if re.match(pattern, text):
            return True

    # Check if mostly uppercase or title case
    if text.isupper() or text.istitle():
        return True

    # Check font properties
    is_bold = span["flags"] & 2**4
    is_large = span["size"] > 14

    return is_bold or is_large


def group_by_font_properties(headings):
    """Group headings by font properties"""
    font_groups = defaultdict(list)

    for heading in headings:
        # Create a key based on font properties
        font_key = (
            round(heading["size"], 1),
            heading["font"].lower(),
            bool(heading["flags"] & 2**4),  # bold
            bool(heading["flags"] & 2**1),  # italic
        )
        font_groups[font_key].append(heading)

    return font_groups


def assign_heading_levels(font_groups):
    """Assign H1, H2, H3 levels to font groups"""
    if not font_groups:
        return []

    # Sort groups by font size (descending) and bold status
    sorted_groups = sorted(font_groups.items(),
                           key=lambda x: (x[0][0], x[0][2]),
                           reverse=True)

    headings = []
    level_map = {0: 'H1', 1: 'H2', 2: 'H3'}

    for group_idx, (font_key, group_headings) in enumerate(sorted_groups[:3]):
        level = level_map.get(group_idx, 'H3')

        for heading in group_headings:
            headings.append({
                "level": level,
                "text": heading["text"],
                "page": heading["page"]
            })

    # Sort by page number to maintain document order
    headings.sort(key=lambda x: x["page"])

    return headings


def classify_headings_multilingual(spans):
    """Classify spans into heading levels with multilingual support"""
    # Filter potential headings
    potential_headings = []

    for span in spans:
        text = span["text"]

        # Enhanced filtering criteria
        if (len(text.strip()) >= 3 and  # Minimum length
            len(text.strip()) <= 200 and  # Maximum reasonable heading length
            not is_page_number(text) and
            not is_footer_header(text) and
                has_heading_characteristics(span)):
            potential_headings.append(span)

    # Cluster by font characteristics
    font_groups = group_by_font_properties(potential_headings)

    # Assign heading levels
    return assign_heading_levels(font_groups)


def detect_language_script(text):
    """Detect script type for language-specific processing"""
    scripts = {
        'latin': 0,
        'cjk': 0,
        'arabic': 0,
        'cyrillic': 0
    }

    for char in text:
        if '\u0020' <= char <= '\u007F' or '\u00A0' <= char <= '\u017F':
            scripts['latin'] += 1
        elif '\u4E00' <= char <= '\u9FFF' or '\u3040' <= char <= '\u309F' or '\u30A0' <= char <= '\u30FF':
            scripts['cjk'] += 1
        elif '\u0600' <= char <= '\u06FF' or '\u0750' <= char <= '\u077F':
            scripts['arabic'] += 1
        elif '\u0400' <= char <= '\u04FF':
            scripts['cyrillic'] += 1

    return max(scripts, key=scripts.get)


class MultilingualPDFParser:
    def __init__(self):
        self.heading_threshold = 0.7

    def extract_outline(self, pdf_path):
        """Main extraction method"""
        doc = fitz.open(pdf_path)

        # Extract all text spans with formatting
        spans = self.extract_formatted_spans(doc)

        # Detect headings with multilingual support
        headings = self.detect_headings(spans)

        # Get document title
        title = self.extract_title(doc, headings)

        doc.close()

        return {
            "title": title,
            "outline": headings
        }

    def extract_formatted_spans(self, doc):
        """Extract all spans with formatting information"""
        all_spans = []

        for page_num in range(doc.page_count):
            page = doc[page_num]
            blocks = page.get_text("dict")["blocks"]

            for block in blocks:
                if "lines" in block:
                    for line in block["lines"]:
                        for span in line["spans"]:
                            if span["text"].strip():
                                span_info = {
                                    "text": preprocess_multilingual_text(span["text"]),
                                    "size": span["size"],
                                    "font": span["font"],
                                    "flags": span["flags"],
                                    "page": page_num + 1,
                                    "bbox": span["bbox"]
                                }
                                all_spans.append(span_info)

        return all_spans

    def detect_headings(self, spans):
        """Main heading detection with multilingual support"""
        return classify_headings_multilingual(spans)

    def extract_title(self, doc, headings):
        """Extract document title"""
        # Try metadata first
        metadata = doc.metadata
        if metadata and metadata.get('title') and metadata['title'].strip():
            return metadata['title'].strip()

        # Use first H1 heading
        for heading in headings:
            if heading['level'] == 'H1':
                return heading['text']

        # Use first heading of any level
        if headings:
            return headings[0]['text']

        # Fallback to filename without extension
        return "Document"


def main():
    input_dir = "./input"
    output_dir = "./output"

    # Create output directory if it doesn't exist
    os.makedirs(output_dir, exist_ok=True)

    parser = MultilingualPDFParser()

    # Check if input directory exists
    if not os.path.exists(input_dir):
        print(f"Input directory {input_dir} not found!")
        return

    # Process all PDF files in input directory
    pdf_files = [f for f in os.listdir(input_dir) if f.endswith('.pdf')]

    if not pdf_files:
        print("No PDF files found in input directory!")
        return

    print(f"Found {len(pdf_files)} PDF file(s) to process")

    for filename in pdf_files:
        pdf_path = os.path.join(input_dir, filename)
        output_filename = filename.replace('.pdf', '.json')
        output_path = os.path.join(output_dir, output_filename)

        print(f"Processing: {filename}")

        try:
            outline = parser.extract_outline(pdf_path)

            with open(output_path, 'w', encoding='utf-8') as f:
                json.dump(outline, f, ensure_ascii=False, indent=2)

            print(f"✓ Successfully processed {filename} -> {output_filename}")
            print(f"  Title: {outline['title']}")
            print(f"  Headings found: {len(outline['outline'])}")

        except Exception as e:
            print(f"✗ Error processing {filename}: {e}")
            import traceback
            traceback.print_exc()


if __name__ == "__main__":
    main()
