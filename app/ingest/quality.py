"""Qualidade da camada de texto. PDFs digitalizados antigos trazem OCR embutido corrompido
(letras cirílicas no lugar de latinas, 'R$5.000.00'), que passaria pelo teste 'tem texto'."""
import re

_CYR = re.compile(r"[Ѐ-ӿ]")
_COMMON = {"de", "da", "do", "que", "em", "para", "com", "os", "as", "ao", "no", "na", "por", "uma", "dos", "se", "ou"}

# Confusões cirílico->latino de alta confiança (usado só como último recurso, sem Tesseract).
_REPAIR = str.maketrans({
    "а": "a", "е": "e", "о": "o", "с": "c", "р": "p", "х": "x", "у": "y", "к": "k", "і": "i",
    "п": "n", "ш": "m", "А": "A", "Е": "E", "О": "O", "С": "C", "Р": "P", "Х": "X", "М": "M", "К": "K",
    "Т": "T", "Н": "H", "В": "B", "ё": "e", "ї": "i",
})


def cyrillic_ratio(text: str) -> float:
    letters = sum(c.isalpha() for c in text) or 1
    return len(_CYR.findall(text)) / letters


def is_degraded(text: str) -> bool:
    """True se a camada de texto parece lixo de OCR e vale refazer o OCR da imagem."""
    if len(text.strip()) < 40:
        return False
    if cyrillic_ratio(text) > 0.01:
        return True
    words = re.findall(r"[a-záéíóúâêôãõç]+", text.lower())
    if len(words) >= 30 and sum(w in _COMMON for w in words) / len(words) < 0.03:
        return True
    return False


def repair_cyrillic(text: str) -> str:
    return text.translate(_REPAIR)
