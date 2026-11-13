MODEL_PRIMARY = "gemini-3-flash"
EXCEL_COLUMNS = [
    "date",
    "description",
    "subtotal",
    "gst",
    "pst",
    "total",
    "payment_card_last_four",
    "has_line_items",
    "doc_type",
    "note",            
    "source_file",
    "page_num",
    "needs_review",
    "thumbnail_path",
]
PROMPT_PATH = "receipts/prompts/receipt_extraction.md"

THUMBNAIL_WIDTH = 600

PDF_RENDER_DPI = 150
POPPLER_PATH = r"C:\poppler\Library\bin"

REVIEW_RULES = {
    "max_total" : 50_000,
    "note_keywords":[
        "continued",
        "continuation",
        "see next page",
        "previous page",
        "multi-page",
        "page continues",
        "sold to",
        "summing",
        "summed",
        "handwritten amount",
        "handwritten total",
        "handwritten tax",
        "handwritten date",
        "handwritten vendor",
        "handwritten correction",
        "handwritten receipt",
        "handwritten invoice",
        "handwritten values",
        "manual receipt",
        "manual invoice",
        "blurry",
        "unclear",
        "upside down",
        "rotated",
        "may be",
        "uncertain",
        "doesn't match",
        "partially",
        "no monetary",
        "not sure",
        "do not match"
    ],
}

ROW_COLORS ={
    "review": "FFF59D",
    "failed": "FFCDD2",
}