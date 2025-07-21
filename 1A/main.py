import unicodedata
import re
import fitz  # PyMuPDF
import json
import sys
import os
from collections import defaultdict


def preprocess_multilingual_text(text):
    """Clean and normalize multilingual text - Enhanced for global languages"""
    # Normalize Unicode (handles all Unicode scripts)
    text = unicodedata.normalize('NFKC', text)

    # Detect script for specific processing
    script = detect_language_script(text)

    # Script-specific preprocessing
    if script == 'arabic' or script == 'hebrew':
        # Handle RTL languages
        # Remove extra spaces but preserve RTL marks
        text = re.sub(r'[\u200E\u200F\u202A-\u202E]+', '',
                      text)  # Remove directional marks
        text = re.sub(r'\s+', ' ', text.strip())

    elif script in ['devanagari', 'tamil', 'telugu', 'kannada', 'malayalam', 'bengali']:
        # Indian scripts - handle combining characters
        text = unicodedata.normalize('NFC', text)  # Canonical composition
        text = re.sub(r'\s+', ' ', text.strip())

    elif script == 'thai' or script == 'lao':
        # Thai/Lao don't use spaces between words
        # Just normalize whitespace at boundaries
        text = text.strip()
        text = re.sub(r'^[\s\u00A0]+|[\s\u00A0]+', ' ', text)

    elif script == 'cjk':
        # CJK scripts - handle full-width characters
        # Convert full-width spaces and punctuation
        text = text.replace('\u3000', ' ')  # Full-width space
        text = re.sub(r'[\uFF01-\uFF5E]',
                      lambda m: chr(ord(m.group()) - 0xFEE0), text)
        text = re.sub(r'\s+', ' ', text.strip())

    elif script in ['myanmar', 'khmer', 'tibetan', 'mongolian', 'ethiopic', 'sinhala']:
        # Other complex scripts
        text = unicodedata.normalize('NFC', text)
        text = re.sub(r'\s+', ' ', text.strip())

    else:
        # Default processing for Latin and other scripts
        text = re.sub(r'\s+', ' ', text.strip())

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
    """Check if span has heading characteristics - Enhanced for global languages"""
    text = span["text"]
    script = detect_language_script(text)

    # Script-specific heading patterns
    heading_patterns = []

    if script in ['latin', 'cyrillic', 'greek']:
        heading_patterns = [
            r'^\d+\.?\s+',  # "1. " or "1 "
            # Uppercase
            r'^[A-Z\u00C0-\u017F\u0100-\u024F\u0400-\u04FF\u0370-\u03FF]{2,}',
            r'^\w+\s*:',  # "Introduction:"
            r'^Chapter\s+\d+',  # Chapter headings
            r'^Section\s+\d+',  # Section headings
        ]

    elif script == 'cjk':
        heading_patterns = [
            # Numbered CJK
            r'^\d+\.?\s*[\u4E00-\u9FFF\u3040-\u309F\u30A0-\u30FF]+',
            # CJK characters only
            r'^[\u4E00-\u9FFF\u3040-\u309F\u30A0-\u30FF]{2,}',
            r'^第\d+章',  # Chapter in Chinese/Japanese
            r'^第\d+节',  # Section in Chinese
        ]

    elif script == 'arabic':
        heading_patterns = [
            r'^\d+[\u0600-\u06FF\u0750-\u077F]+',  # Numbered Arabic
            r'^[\u0600-\u06FF\u0750-\u077F]{3,},'
            r'^الفصل\s+\d+',  # Chapter in Arabic
        ]

    elif script in ['devanagari', 'tamil', 'telugu', 'kannada', 'malayalam', 'bengali']:
        # Indian scripts
        unicode_ranges = {
            'devanagari': (0x0900, 0x097F),
            'tamil': (0x0B80, 0x0BFF),
            'telugu': (0x0C00, 0x0C7F),
            'kannada': (0x0C80, 0x0CFF),
            'malayalam': (0x0D00, 0x0D7F),
            'bengali': (0x0980, 0x09FF),
        }

        if script in unicode_ranges:
            start, end = unicode_ranges[script]
            pattern = f'^\\d+[\\u{start:04X}-\\u{end:04X}]+'
            heading_patterns = [pattern]

    # Check patterns
    for pattern in heading_patterns:
        try:
            if re.match(pattern, text, re.UNICODE):
                return True
        except:
            pass  # Skip invalid patterns

    # Universal checks
    if text.isupper() or text.istitle():
        return True

    # Font-based detection (works for all scripts)
    is_bold = span["flags"] & 2**4
    is_italic = span["flags"] & 2**1
    is_large = span["size"] > 14

    # Script-specific size thresholds
    size_threshold = 12
    if script == 'cjk':
        size_threshold = 16  # CJK fonts often appear larger
    elif script in ['devanagari', 'tamil', 'telugu']:
        size_threshold = 14  # Indian scripts

    return is_bold or (span["size"] > size_threshold) or (is_italic and span["size"] > size_threshold - 2)


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
    """Detect script type for language-specific processing - Enhanced for more languages"""
    scripts = {
        'latin': 0,
        'cjk': 0,
        'arabic': 0,
        'cyrillic': 0,
        'devanagari': 0,  # Hindi, Sanskrit, Marathi
        'thai': 0,
        'hebrew': 0,
        'greek': 0,
        'georgian': 0,
        'armenian': 0,
        'tamil': 0,
        'telugu': 0,
        'kannada': 0,
        'malayalam': 0,
        'bengali': 0,
        'gurmukhi': 0,  # Punjabi
        'gujarati': 0,
        'oriya': 0,
        'sinhala': 0,
        'myanmar': 0,
        'khmer': 0,  # Cambodian
        'lao': 0,
        'tibetan': 0,
        'mongolian': 0,
        'ethiopic': 0
    }

    for char in text:
        code = ord(char)

        # Latin scripts (including extended Latin)
        if (0x0020 <= code <= 0x007F or 0x00A0 <= code <= 0x017F or
                0x0100 <= code <= 0x024F or 0x1E00 <= code <= 0x1EFF):
            scripts['latin'] += 1

        # CJK (Chinese, Japanese, Korean)
        elif (0x4E00 <= code <= 0x9FFF or 0x3040 <= code <= 0x309F or
              0x30A0 <= code <= 0x30FF or 0xAC00 <= code <= 0xD7AF):
            scripts['cjk'] += 1

        # Arabic and Persian
        elif (0x0600 <= code <= 0x06FF or 0x0750 <= code <= 0x077F or
              0x08A0 <= code <= 0x08FF):
            scripts['arabic'] += 1

        # Cyrillic
        elif 0x0400 <= code <= 0x04FF:
            scripts['cyrillic'] += 1

        # Devanagari (Hindi, Sanskrit, Marathi)
        elif 0x0900 <= code <= 0x097F:
            scripts['devanagari'] += 1

        # Thai
        elif 0x0E00 <= code <= 0x0E7F:
            scripts['thai'] += 1

        # Hebrew
        elif 0x0590 <= code <= 0x05FF:
            scripts['hebrew'] += 1

        # Greek
        elif 0x0370 <= code <= 0x03FF:
            scripts['greek'] += 1

        # Georgian
        elif 0x10A0 <= code <= 0x10FF:
            scripts['georgian'] += 1

        # Armenian
        elif 0x0530 <= code <= 0x058F:
            scripts['armenian'] += 1

        # Tamil
        elif 0x0B80 <= code <= 0x0BFF:
            scripts['tamil'] += 1

        # Telugu
        elif 0x0C00 <= code <= 0x0C7F:
            scripts['telugu'] += 1

        # Kannada
        elif 0x0C80 <= code <= 0x0CFF:
            scripts['kannada'] += 1

        # Malayalam
        elif 0x0D00 <= code <= 0x0D7F:
            scripts['malayalam'] += 1

        # Bengali
        elif 0x0980 <= code <= 0x09FF:
            scripts['bengali'] += 1

        # Gurmukhi (Punjabi)
        elif 0x0A00 <= code <= 0x0A7F:
            scripts['gurmukhi'] += 1

        # Gujarati
        elif 0x0A80 <= code <= 0x0AFF:
            scripts['gujarati'] += 1

        # Oriya
        elif 0x0B00 <= code <= 0x0B7F:
            scripts['oriya'] += 1

        # Sinhala
        elif 0x0D80 <= code <= 0x0DFF:
            scripts['sinhala'] += 1

        # Myanmar
        elif 0x1000 <= code <= 0x109F:
            scripts['myanmar'] += 1

        # Khmer (Cambodian)
        elif 0x1780 <= code <= 0x17FF:
            scripts['khmer'] += 1

        # Lao
        elif 0x0E80 <= code <= 0x0EFF:
            scripts['lao'] += 1

        # Tibetan
        elif 0x0F00 <= code <= 0x0FFF:
            scripts['tibetan'] += 1

        # Mongolian
        elif 0x1800 <= code <= 0x18AF:
            scripts['mongolian'] += 1

        # Ethiopic
        elif 0x1200 <= code <= 0x137F:
            scripts['ethiopic'] += 1

    return max(scripts, key=scripts.get) if scripts else 'latin'


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
