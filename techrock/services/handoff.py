"""Explicit requests bypass generation; other phrasing is classified by the AI."""
import re

def requests_person(text):
    text = ' '.join(text.casefold().split())
    patterns = (
        r'\b(?:talk|speak|chat|connect)\s+(?:me\s+)?(?:to|with)\s+(?:a\s+|an\s+|the\s+|real\s+)*(?:person|human|agent|admin|staff|representative)\b',
        r'\b(?:human|person|agent|admin)\s+please\b',
        r'(?:মানুষ|এডমিন|অ্যাডমিন|কর্মী|প্রতিনিধি).*(?:কথা|যোগাযোগ)',
    )
    # Negated requests must be interpreted in context by the model.
    if re.search(r"\b(?:not|don't|dont|no|never)\b", text) or 'চাই না' in text:
        return False
    return any(re.search(pattern, text) for pattern in patterns)
