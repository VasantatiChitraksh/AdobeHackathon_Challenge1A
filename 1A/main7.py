import os
import re
import json
import unicodedata
from pdfminer.high_level import extract_pages
from pdfminer.layout import LTTextContainer, LTChar, LTTextBox
from collections import defaultdict, Counter
import statistics

class SmartPDFOutlineExtractor:
    def __init__(self):
        # Configure thresholds
        self.min_heading_length = 5
        self.max_heading_length = 150
        self.min_font_size = 8
        self.max_headings_per_level = 15
        
        # Common form field patterns to exclude
        self.form_field_patterns = [
            r'^\d+\.\s*$',  # Just numbers
            r'^[A-Z]{1,4}$',  # Short acronyms
            r'^(Name|Date|Age|Signature|Rs\.|S\.No|Relationship|Designation)$',
            r'^\w+:\s*$',  # Labels ending with colon
            r'^[-_=]{2,}$',  # Separators
            r'^\d{1,2}/\d{1,2}/\d{2,4}$',  # Dates
            r'^Page\s+\d+$',  # Page numbers
            r'^\w+@\w+\.\w+$',  # Email addresses
            r'^www\.\w+',  # URLs
            r'^https?://',  # URLs
            r'^\(\d{3}\)\s*\d{3}-\d{4}$',  # Phone numbers
            r'^\d{5}(-\d{4})?$',  # ZIP codes
        ]
        
        # Address/contact patterns
        self.address_patterns = [
            r'^\d+\s+[A-Z\s]+$',  # "3735 PARKWAY"
            r'^[A-Z\s]+,\s*[A-Z]{2}\s+\d{5}',  # "CITY, ST 12345"
            r'^\([A-Z\s]+\)$',  # "(NEAR SOMETHING)"
        ]
        
        # Heading patterns that are likely legitimate
        self.heading_patterns = [
            r'^\d+\.\s+[A-Z].*',  # "1. Introduction"
            r'^Chapter\s+\d+',  # "Chapter 1"
            r'^Section\s+\d+',  # "Section 1"
            r'^[A-Z][a-z]+(\s+[A-Z][a-z]*)*\s*:?\s*$',  # Title case
            r'^[A-Z\s]+[A-Z]$',  # ALL CAPS (but substantial)
            r'^\d+\.\d+\s+',  # "1.1 Something"
            r'^Overview\s*$',
            r'^Introduction\s*$',
            r'^Conclusion\s*$',
            r'^References\s*$',
            r'^Acknowledgements\s*$',
            r'^Abstract\s*$',
            r'^Summary\s*$',
            r'Application\s+form',  # Form titles
        ]

    def extract_text_elements(self, pdf_path):
        """Extract text elements with detailed formatting info"""
        elements = []
        page_fonts = defaultdict(list)
        
        for page_num, layout in enumerate(extract_pages(pdf_path)):
            for element in layout:
                if isinstance(element, LTTextContainer):
                    for line in element:
                        text = line.get_text().strip()
                        if not text or len(text) < 2:
                            continue
                        
                        # Get font info from characters
                        font_sizes = []
                        font_names = []
                        
                        for char in line:
                            if isinstance(char, LTChar):
                                font_sizes.append(char.size)
                                font_names.append(char.fontname)
                        
                        if not font_sizes:
                            continue
                        
                        # Use most common/largest font in line
                        max_size = max(font_sizes)
                        common_font = Counter(font_names).most_common(1)[0][0] if font_names else ""
                        
                        # Skip very small text
                        if max_size < self.min_font_size:
                            continue
                        
                        element_info = {
                            'text': self.clean_text(text),
                            'font_size': max_size,
                            'font_name': common_font.lower(),
                            'page': page_num + 1,
                            'bbox': getattr(line, 'bbox', None),
                            'is_bold': 'bold' in common_font.lower(),
                            'position_y': getattr(line, 'bbox', [0,0,0,0])[1] if hasattr(line, 'bbox') else 0
                        }
                        
                        elements.append(element_info)
                        page_fonts[page_num].append(max_size)
        
        return elements, page_fonts

    def clean_text(self, text):
        """Clean and normalize text"""
        # Unicode normalization
        text = unicodedata.normalize('NFKC', text)
        
        # Clean up whitespace
        text = re.sub(r'\s+', ' ', text.strip())
        
        # Remove excessive punctuation
        text = re.sub(r'[.]{3,}', '...', text)
        
        return text

    def calculate_document_stats(self, elements, page_fonts):
        """Calculate document-wide statistics"""
        all_sizes = [elem['font_size'] for elem in elements]
        
        if not all_sizes:
            return {'base_size': 12, 'large_threshold': 14}
        
        # Calculate base font size (most common)
        size_counter = Counter(all_sizes)
        base_size = size_counter.most_common(1)[0][0]
        
        # Calculate what constitutes "large" text
        avg_size = statistics.mean(all_sizes)
        large_threshold = max(base_size + 2, avg_size + 1)
        
        return {
            'base_size': base_size,
            'large_threshold': large_threshold,
            'avg_size': avg_size,
            'size_distribution': dict(size_counter)
        }

    def is_likely_heading(self, element, doc_stats):
        """Determine if an element is likely a heading"""
        text = element['text']
        
        # Basic length filters
        if len(text) < self.min_heading_length or len(text) > self.max_heading_length:
            return False
        
        # Exclude obvious non-headings
        if self.is_form_field(text):
            return False
        
        if self.is_address_or_contact(text):
            return False
        
        # Score the heading candidate
        score = 0
        
        # Content-based scoring
        if self.matches_heading_pattern(text):
            score += 3
        
        # Font-based scoring
        if element['font_size'] > doc_stats['large_threshold']:
            score += 2
        elif element['font_size'] > doc_stats['base_size']:
            score += 1
        
        if element['is_bold']:
            score += 2
        
        # Position-based scoring (top of page more likely to be heading)
        if element.get('position_y', 0) > 700:  # Rough top of page
            score += 1
        
        # Text characteristics
        if text.istitle() and len(text.split()) > 1:
            score += 1
        
        if text.isupper() and 5 <= len(text) <= 50:
            score += 1
        
        # Penalize certain patterns
        if re.match(r'^\d+$', text):  # Just numbers
            score -= 2
        
        if text.count('.') > 3:  # Too many dots
            score -= 1
        
        return score >= 2

    def is_form_field(self, text):
        """Check if text is likely a form field"""
        for pattern in self.form_field_patterns:
            if re.match(pattern, text, re.IGNORECASE):
                return True
        return False

    def is_address_or_contact(self, text):
        """Check if text is likely address or contact info"""
        for pattern in self.address_patterns:
            if re.match(pattern, text, re.IGNORECASE):
                return True
        return False

    def matches_heading_pattern(self, text):
        """Check if text matches common heading patterns"""
        for pattern in self.heading_patterns:
            if re.match(pattern, text, re.IGNORECASE):
                return True
        return False

    def determine_heading_level(self, text, font_size, doc_stats):
        """Determine heading level based on content and formatting"""
        
        # Check for numbered sections
        if re.match(r'^(\d+)\.(\d+)\.(\d+)', text):
            return 'H3'
        elif re.match(r'^(\d+)\.(\d+)', text):
            return 'H2'
        elif re.match(r'^(\d+)\.', text):
            return 'H1'
        
        # Check for chapter/section keywords
        if re.match(r'^(Chapter|Part)\s+\d+', text, re.IGNORECASE):
            return 'H1'
        elif re.match(r'^Section\s+\d+', text, re.IGNORECASE):
            return 'H2'
        
        # Font size based
        if font_size > doc_stats.get('large_threshold', 14) + 2:
            return 'H1'
        elif font_size > doc_stats.get('large_threshold', 14):
            return 'H2'
        else:
            return 'H3'

    def extract_title(self, elements, headings):
        """Extract document title with intelligent logic"""
        
        # Look for document title patterns first
        title_candidates = []
        
        for elem in elements[:10]:  # Check first 10 elements
            text = elem['text']
            
            # Skip obvious non-titles
            if self.is_form_field(text) or self.is_address_or_contact(text):
                continue
            
            # Look for title-like content
            if (len(text) > 10 and 
                not re.match(r'^\d+\.', text) and  # Not numbered
                ('form' in text.lower() or 'application' in text.lower() or 
                 'overview' in text.lower() or 'document' in text.lower() or
                 len(text.split()) >= 3)):  # Multi-word
                title_candidates.append(text)
        
        # Use first good title candidate
        if title_candidates:
            return title_candidates[0]
        
        # Fallback to first substantial H1
        h1_headings = [h for h in headings if h['level'] == 'H1' and len(h['text']) > 10]
        if h1_headings:
            return h1_headings[0]['text']
        
        # Last resort: any first heading
        if headings:
            return headings[0]['text']
        
        return "Document"

    def process_document(self, pdf_path):
        """Main processing pipeline"""
        try:
            # Extract text elements
            elements, page_fonts = self.extract_text_elements(pdf_path)
            
            if not elements:
                return {"title": "Document", "outline": []}
            
            # Calculate document statistics
            doc_stats = self.calculate_document_stats(elements, page_fonts)
            
            # Identify headings
            heading_candidates = []
            for element in elements:
                if self.is_likely_heading(element, doc_stats):
                    level = self.determine_heading_level(
                        element['text'], element['font_size'], doc_stats
                    )
                    heading_candidates.append({
                        'level': level,
                        'text': element['text'],
                        'page': element['page'],
                        'font_size': element['font_size']
                    })
            
            # Remove duplicates and limit per level
            final_headings = self.deduplicate_and_limit(heading_candidates)
            
            # Extract title
            title = self.extract_title(elements, final_headings)
            
            return {
                "title": title,
                "outline": final_headings
            }
            
        except Exception as e:
            print(f"Error processing {pdf_path}: {e}")
            return {"title": "Document", "outline": []}

    def deduplicate_and_limit(self, headings):
        """Remove duplicates and limit headings per level"""
        seen_texts = set()
        level_counts = defaultdict(int)
        final_headings = []
        
        # Sort by page then by font size (descending)
        headings.sort(key=lambda x: (x['page'], -x['font_size']))
        
        for heading in headings:
            text_key = heading['text'].lower().strip()
            level = heading['level']
            
            # Skip duplicates
            if text_key in seen_texts:
                continue
            
            # Limit per level
            if level_counts[level] >= self.max_headings_per_level:
                continue
            
            seen_texts.add(text_key)
            level_counts[level] += 1
            
            # Remove font_size from final output
            final_heading = {k: v for k, v in heading.items() if k != 'font_size'}
            final_headings.append(final_heading)
        
        return final_headings


def main():
    input_dir = "input"
    output_dir = "output"
    
    # Create output directory
    os.makedirs(output_dir, exist_ok=True)
    
    if not os.path.exists(input_dir):
        print(f"Input directory '{input_dir}' not found")
        return
    
    # Find PDF files
    pdf_files = [f for f in os.listdir(input_dir) if f.lower().endswith('.pdf')]
    
    if not pdf_files:
        print("No PDF files found in input directory")
        return
    
    print(f"Found {len(pdf_files)} PDF file(s) to process")
    
    extractor = SmartPDFOutlineExtractor()
    
    for filename in pdf_files:
        pdf_path = os.path.join(input_dir, filename)
        output_path = os.path.join(output_dir, filename.replace('.pdf', '.json'))
        
        print(f"Processing: {filename}")
        
        result = extractor.process_document(pdf_path)
        
        # Save result
        with open(output_path, 'w', encoding='utf-8') as f:
            json.dump(result, f, ensure_ascii=False, indent=2)
        
        print(f"  ✓ Title: {result['title']}")
        print(f"  ✓ Headings found: {len(result['outline'])}")
        
        # Debug: show headings
        for heading in result['outline'][:5]:  # Show first 5
            print(f"    {heading['level']}: {heading['text'][:50]}...")


if __name__ == "__main__":
    main()