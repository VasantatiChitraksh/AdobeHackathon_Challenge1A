import unicodedata
import re
import fitz  # PyMuPDF
import json
import sys
import os
from collections import defaultdict, Counter


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


def is_form_field(text):
    """Check if text is likely a form field label"""
    text = text.strip()
    if not text:
        return False
    
    # Common form field patterns
    form_patterns = [
        r'^\d+\.\s*$',  # Just numbers like "1.", "2."
        r'^[A-Z]{2,4}$',  # Short acronyms like "PAY", "SI", "NPA"
        r'^Rs\.$',  # Currency
        r'^S\.No$',  # Serial number
        r'^Date$',  # Single word fields
        r'^Name$',
        r'^Age$',
        r'^Relationship$',
        r'^Designation$',
        r'^Service$',
        r'^Single$',
    ]
    
    for pattern in form_patterns:
        if re.match(pattern, text, re.IGNORECASE):
            return True
    
    return False


def is_meaningful_heading(text):
    """Check if text could be a meaningful heading"""
    text = text.strip()
    if not text:
        return False
    
    # Must have reasonable length for a heading
    if len(text) < 3 or len(text) > 200:
        return False
    
    # Should contain at least one letter
    if not re.search(r'[a-zA-Z\u00C0-\u017F\u0100-\u024F\u0400-\u04FF\u0370-\u03FF\u4E00-\u9FFF\u3040-\u309F\u30A0-\u30FF\u0600-\u06FF]', text):
        return False
    
    # Reject if it's mostly punctuation or numbers
    alpha_chars = len(re.findall(r'[a-zA-Z\u00C0-\u017F\u0100-\u024F\u0400-\u04FF\u0370-\u03FF\u4E00-\u9FFF\u3040-\u309F\u30A0-\u30FF\u0600-\u06FF]', text))
    if alpha_chars < len(text) * 0.3:  # At least 30% should be letters
        return False
    
    # Reject obvious non-headings
    if re.match(r'^[-=_]+$', text):  # Just separators
        return False
    
    if re.match(r'^\d+\s*-\s*\d+$', text):  # Page ranges
        return False
    
    return True


def has_heading_characteristics(span, document_stats):
    """Enhanced heading detection with document context"""
    text = span["text"]
    
    # Basic filtering
    if not is_meaningful_heading(text):
        return False
    
    if is_form_field(text):
        return False
    
    script = detect_language_script(text)
    
    # Get font characteristics
    font_size = span["size"]
    is_bold = span["flags"] & 2**4
    is_italic = span["flags"] & 2**1
    
    # Use document statistics for better detection
    avg_font_size = document_stats.get('avg_font_size', 12)
    common_font_size = document_stats.get('most_common_font_size', 12)
    
    # Heading indicators
    heading_score = 0
    
    # Size-based scoring
    if font_size > avg_font_size + 2:
        heading_score += 2
    elif font_size > avg_font_size:
        heading_score += 1
    
    # Bold text is likely a heading
    if is_bold:
        heading_score += 2
    
    # Italic large text might be heading
    if is_italic and font_size > avg_font_size:
        heading_score += 1
    
    # Pattern-based detection
    heading_patterns = []
    
    if script in ['latin', 'cyrillic', 'greek']:
        heading_patterns = [
            r'^\d+\.\s+\w+',  # "1. Introduction"  
            r'^Chapter\s+\d+',  # Chapter headings
            r'^Section\s+\d+',  # Section headings
            r'^\w+\s*:?\s*$',  # Single word/phrase potentially
        ]
        
        # Check for title case or all caps (but not single words)
        if len(text.split()) > 1:
            if text.istitle() or text.isupper():
                heading_score += 1

    elif script == 'cjk':
        heading_patterns = [
            r'^\d+\.\s*[\u4E00-\u9FFF\u3040-\u309F\u30A0-\u30FF]+',
            r'^第\d+章',  # Chapter in Chinese/Japanese
            r'^第\d+节',  # Section in Chinese
        ]

    elif script == 'arabic':
        heading_patterns = [
            r'^\d+[\u0600-\u06FF\u0750-\u077F]+',
            r'^الفصل\s+\d+',  # Chapter in Arabic
        ]

    # Check patterns
    for pattern in heading_patterns:
        try:
            if re.match(pattern, text, re.UNICODE):
                heading_score += 2
                break
        except:
            pass
    
    # Require minimum score for heading
    return heading_score >= 2


def calculate_document_stats(spans):
    """Calculate document-wide statistics for better heading detection"""
    font_sizes = [span["size"] for span in spans if span["text"].strip()]
    
    if not font_sizes:
        return {'avg_font_size': 12, 'most_common_font_size': 12}
    
    avg_font_size = sum(font_sizes) / len(font_sizes)
    size_counter = Counter(font_sizes)
    most_common_font_size = size_counter.most_common(1)[0][0]
    
    return {
        'avg_font_size': avg_font_size,
        'most_common_font_size': most_common_font_size
    }


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
    """Assign H1, H2, H3 levels to font groups with better logic"""
    if not font_groups:
        return []

    # Sort groups by font size (descending), then by bold status
    sorted_groups = sorted(font_groups.items(),
                           key=lambda x: (x[0][0], x[0][2]),  # size, bold
                           reverse=True)

    headings = []
    level_map = {0: 'H1', 1: 'H2', 2: 'H3'}
    
    # Limit to maximum 3 heading levels and reasonable number of headings
    max_groups = min(3, len(sorted_groups))
    
    for group_idx, (font_key, group_headings) in enumerate(sorted_groups[:max_groups]):
        level = level_map.get(group_idx, 'H3')
        
        # Limit headings per group to avoid spam
        limited_headings = group_headings[:10]  # Max 10 headings per level
        
        for heading in limited_headings:
            headings.append({
                "level": level,
                "text": heading["text"],
                "page": heading["page"]
            })

    # Sort by page number to maintain document order
    headings.sort(key=lambda x: x["page"])

    return headings


def classify_headings_multilingual(spans):
    """Classify spans into heading levels with improved filtering"""
    # Calculate document statistics
    document_stats = calculate_document_stats(spans)
    
    # Filter potential headings with stricter criteria
    potential_headings = []

    for span in spans:
        text = span["text"]

        # Enhanced filtering
        if (not is_page_number(text) and
            not is_footer_header(text) and
            not is_form_field(text) and
            is_meaningful_heading(text) and
            has_heading_characteristics(span, document_stats)):
            potential_headings.append(span)

    # If too many potential headings, apply stricter filtering
    if len(potential_headings) > 20:
        # Keep only the most likely headings (bold, large, or well-formatted)
        filtered_headings = []
        for heading in potential_headings:
            is_bold = heading["flags"] & 2**4
            is_large = heading["size"] > document_stats['avg_font_size'] + 1
            has_number = re.match(r'^\d+\.', heading["text"])
            
            if is_bold or is_large or has_number:
                filtered_headings.append(heading)
        
        potential_headings = filtered_headings[:15]  # Limit to 15 max

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
        'cyrillic': 0,
        'devanagari': 0,
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
        'gurmukhi': 0,
        'gujarati': 0,
        'oriya': 0,
        'sinhala': 0,
        'myanmar': 0,
        'khmer': 0,
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

        # Add other script ranges as in original...

    return max(scripts, key=scripts.get) if scripts else 'latin'


class MultilingualPDFParser:
    def __init__(self):
        self.heading_threshold = 0.7

    def extract_outline(self, pdf_path):
        """Main extraction method"""
        doc = fitz.open(pdf_path)

        # Extract all text spans with formatting
        spans = self.extract_formatted_spans(doc)

        # Detect headings with improved multilingual support
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
        """Main heading detection with improved filtering"""
        return classify_headings_multilingual(spans)

    def extract_title(self, doc, headings):
        """Extract document title with better fallback"""
        # Try metadata first
        metadata = doc.metadata
        if metadata and metadata.get('title') and metadata['title'].strip():
            title = metadata['title'].strip()
            # Clean up common metadata artifacts
            if not title.endswith(('.doc', '.pdf', '.docx')):
                return title

        # Use first H1 heading if it looks like a title
        for heading in headings:
            if heading['level'] == 'H1' and len(heading['text']) > 10:
                return heading['text']

        # Use any first heading if it's substantial
        if headings and len(headings[0]['text']) > 5:
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