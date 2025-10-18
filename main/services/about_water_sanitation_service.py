import os
import json
import math
import requests
from datetime import datetime
import pytz
import random

# -----------------------------------------------------------------------------
# Content service for the "About Water & Sanitation" page.
# All display strings are centralized here so templates don't hardcode text.
# -----------------------------------------------------------------------------

ABOUT_CONTENT = {
    "title": "About Us",
    "subtitle": "Helping families in Victoria make safe and informed choices about water activities.",
    "sections": [
        {
            "heading": "Our Mission",
            "type": "paragraph",
            "body": (
                "Help families in Victoria make safe and informed choices about water activities — "
                "such as camping, fishing, and swimming — by providing clear, reliable, and "
                "easy-to-understand water information."
            ),
        },
        {
            "heading": "Our Vision",
            "type": "paragraph",
            "body": (
                'A future where every family and child in Melbourne understands the importance of '
                'clean water and takes action to protect it, contributing to '
            ),
            "link": {
                "text": "SDG 6",
                "url": "https://sdgs.un.org/goals/goal6",
            },
            "body_suffix": ".",
        },
        {
            "heading": "Project Background",
            "type": "paragraph",
            "body": (
                "Developed by a student research team to improve public awareness of water quality "
                "in Victoria. Combines scientific data with accessible design for all ages, including children."
            ),
        },
    ],
    "value_impact": [
        "Interactive learning section for kids — children can earn “Water Protector” cards.",
        "Melbourne Fish Map — shows local species and their info.",
        "Simple data visualisations to understand water quality and risks.",
    ],
    "future_plans": [
        "Expand water prediction coverage in Australia.",
        "Add more games for children.",
        "Collaborate with environmental organisations and local councils.",
        "Develop a mobile-friendly version.",
    ],
    "data_sources": [
        {"name": "Melbourne Water", "url": "https://www.melbournewater.com.au/"},
        {"name": "EPA Victoria", "url": "https://www.epa.vic.gov.au/"},
        {"name": "Bureau of Meteorology", "url": "https://www.bom.gov.au/"},
        {"name": "Flaticon", "url": "https://www.flaticon.com/free-icons"},
        {"name": "National Water Week", "url": "https://www.nationalwaterweek.org/resources/resources#;"},
        {"name": "Healthy Waterways", "url": "https://healthywaterways.com.au/key-values/platypus"},
    ],
    "sponsors": [
        {
            "name": "Australian Water Association (AWA)",
            "url": "https://www.awa.asn.au/",
            "desc": (
                "The Clean Water Project aligns with AWA’s mission of advancing sustainable water "
                "management and supporting the next generation of water professionals. Sponsoring our "
                "project allows AWA to demonstrate its commitment to education, student development, "
                "and community engagement."
            ),
        },
        {
            "name": "WaterAid Australia",
            "url": "https://www.wateraid.org/au/",
            "desc": (
                "WaterAid Australia is committed to improving access to safe water, sanitation, and hygiene "
                "through education and community engagement. Our project aligns with this mission by raising "
                "awareness of water sustainability and empowering students to contribute innovative solutions."
            ),
        },
    ],
}


def get_about_content() -> dict:
    """Return a structured dict the template can consume directly."""
    # You can augment or localize fields here before returning if needed.
    return ABOUT_CONTENT


def get_about_content_json(indent: int = 2) -> str:
    """Optional: JSON string, useful for APIs or script tags."""
    return json.dumps(get_about_content(), ensure_ascii=False, indent=indent)
