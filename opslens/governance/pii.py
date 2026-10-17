"""Mask personal data before anything is sent to an AI model outside our own servers."""
import re

import pandas as pd
import yaml

from opslens.settings import CONFIG_DIR

EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
UK_PHONE = re.compile(r"(?:\+44\s?|\b0)\d{2,4}[\s-]?\d{3,4}[\s-]?\d{3,4}\b")
UK_POSTCODE = re.compile(r"\b[A-Z]{1,2}\d[A-Z\d]?\s*\d[A-Z]{2}\b", re.IGNORECASE)


def mask_text(text: str) -> str:
    text = EMAIL.sub("[email]", text)
    text = UK_PHONE.sub("[phone]", text)
    return UK_POSTCODE.sub("[postcode]", text)


def mask_frame(df: pd.DataFrame) -> pd.DataFrame:
    """Blank out columns whose names look personal, and mask anything personal-looking in text cells."""
    patterns = yaml.safe_load(open(CONFIG_DIR / "access_policy.yaml"))["never_send_externally"]["patterns"]
    out = df.copy()
    for col in out.columns:
        if any(p in col.lower() for p in patterns):
            out[col] = "[masked]"
        elif out[col].dtype == object:
            out[col] = out[col].map(lambda v: mask_text(v) if isinstance(v, str) else v)
    return out
