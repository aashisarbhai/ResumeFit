import os
import json 
import logging
import re
from typing import Dict, List

from groq import Groq

logger=logging.getLogger('ats_resume_scorer')


GROQ_MODEL='llama-3.3-70b-versatile'

_client=None

def _get_client()->Groq:
    global _client
    if _client is None:
        api_key=os.getenv('GROQ_API_KEY')

        if not api_key:
            raise ValueError("GROQ_API_KEY environment variable not set")
        _client=Groq(api_key=api_key)
    return _client

RESUME_SYSTEM_PROMPT = (
    "You are a resume parser. Extract information from the resume "
    "and return ONLY a valid JSON object. No explanation, no markdown."
)

RESUME_USER_PROMPT = """Extract the following from this resume and return as JSON:
{{
  "name": "full name",
  "email": "email address",
  "phone": "phone number",
  "linkedin": "LinkedIn URL if present, otherwise null",
  "github": "GitHub URL if present, otherwise null",
  "professional_summary": "the full text of the Summary, Profile, About Me, Objective, or Professional Summary section at the top of the resume. Copy the ENTIRE paragraph exactly as written. If no such section exists, return an empty string.",
  "skills": ["list", "of", "skills"],
  "experience": [
    {{
      "job_title": "",
      "company": "",
      "start_date": "",
      "end_date": "",
      "duration_months": 0,
      "description": ""
    }}
  ],
  "education": [
    {{
      "degree": "",
      "institution": "",
      "year": ""
    }}
  ],
  "certifications": ["list of certifications"],
  "projects": [
    {{
      "title": "project name",
      "description": "what the project does and how it was built",
      "technologies": ["tech", "used"]
    }}
  ],
  "action_verbs": ["strong action verbs used in bullet points, e.g. developed, implemented, designed"],
  "keywords": ["important keywords and phrases from the resume for ATS matching"]
}}

Important instructions:
- For duration_months, calculate the number of months between start_date and end_date. If end_date is "Present" or "Current", calculate from start_date to now.
- For skills, extract ALL technical and soft skills mentioned anywhere in the resume.
- For action_verbs, find verbs that start bullet points or describe achievements.
- For keywords, extract noun phrases and technical terms relevant to ATS matching.
- Return ONLY valid JSON. No markdown code fences, no explanation.

Resume Text:
{raw_text}"""

def _call_groq(client:Groq, system_prompt:str, user_prompt:str)->str:

    response=client.chat.completions.create(
        model=GROQ_MODEL, 
        messages=[
            {'role': 'system', 'content': system_prompt},
            {'role': 'user', 'content': user_prompt}
        ],
        temperature=0.0,
        max_tokens=4096
    )

    return response.choices[0].message.content.strip()

def _try_parse_json(text: str) -> dict | None:

    # Strip markdown code fences if present
    cleaned = text.strip()
    if cleaned.startswith("```"):

        # Remove opening fence (```json or ```)
        first_newline = cleaned.index("\n") if "\n" in cleaned else len(cleaned)
        cleaned = cleaned[first_newline + 1:]
        # Remove closing fence
        if cleaned.endswith("```"):
            cleaned = cleaned[:-3]
        cleaned = cleaned.strip()

    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        return None
    
def _parse_resume_with_groq(raw_text: str)->Dict:

    client=_get_client()
    prompt=RESUME_USER_PROMPT.format(raw_text=raw_text)
    raw_response=_call_groq(client, RESUME_SYSTEM_PROMPT, prompt)
    result=_try_parse_json(raw_response)

    if result is not None:
        return _validate_resume_result(result)
    

    logger.warning("Groq resume parse: first attempt returned invalid JSON, retrying...")
    strict_prompt = (
        "Your previous response was not valid JSON. "
        "Return ONLY the raw JSON object, no markdown, no explanation, no code fences.\n\n"
        + prompt
    )
    raw_response = _call_groq(client, RESUME_SYSTEM_PROMPT, strict_prompt)
    result = _try_parse_json(raw_response)
    if result is not None:
        return _validate_resume_result(result)

    raise ValueError(
        f"Groq returned unparseable response after retry. Raw response:\n{raw_response[:500]}"
    )
    
JD_SYSTEM_PROMPT = (
    "You are a job description parser. Extract information and "
    "return ONLY a valid JSON object. No explanation, no markdown."
)

JD_USER_PROMPT = """Extract the following from this job description and return as JSON:
{{
  "job_title": "",
  "required_skills": ["list of must-have skills"],
  "preferred_skills": ["list of nice-to-have skills"],
  "experience_required": "",
  "education_required": "",
  "key_responsibilities": ["list of responsibilities"],
  "keywords": ["important keywords and phrases for ATS matching"]
}}

Important instructions:
- required_skills: skills explicitly stated as required or must-have.
- preferred_skills: skills stated as preferred, nice-to-have, or bonus.
- keywords: extract ALL important terms an ATS system would match against,
  including skills, technologies, certifications, and domain terms.
- Return ONLY valid JSON. No markdown code fences, no explanation.

Job Description Text:
{raw_text}"""

def _parse_job_description_with_groq(raw_text: str) -> Dict:
    client = _get_client()
    prompt = JD_USER_PROMPT.format(raw_text=raw_text)

    raw_response = _call_groq(client, JD_SYSTEM_PROMPT, prompt)
    result = _try_parse_json(raw_response)
    if result is not None:
        return _validate_jd_result(result)

    logger.warning("Groq JD parse: first attempt returned invalid JSON, retrying...")
    strict_prompt = (
        "Your previous response was not valid JSON. "
        "Return ONLY the raw JSON object, no markdown, no explanation, no code fences.\n\n"
        + prompt
    )
    raw_response = _call_groq(client, JD_SYSTEM_PROMPT, strict_prompt)
    result = _try_parse_json(raw_response)
    if result is not None:
        return _validate_jd_result(result)

    raise ValueError(
        f"Groq returned unparseable response after retry. Raw response:\n{raw_response[:500]}"
    )


_KNOWN_SKILLS = (
    'python', 'java', 'javascript', 'typescript', 'c++', 'c#', 'sql', 'html', 'css',
    'react', 'angular', 'vue', 'node.js', 'express', 'django', 'flask', 'fastapi',
    'spring boot', 'docker', 'kubernetes', 'aws', 'azure', 'gcp', 'git', 'linux',
    'postgresql', 'mysql', 'mongodb', 'redis', 'tensorflow', 'pytorch', 'scikit-learn',
    'machine learning', 'data analysis', 'power bi', 'tableau', 'excel', 'figma',
    'rest api', 'graphql', 'agile', 'scrum', 'communication', 'leadership',
)


def _section_text(text: str, headings: List[str]) -> str:
    heading_set = {heading.lower() for heading in headings}
    lines = text.splitlines()
    start = next(
        (
            index + 1
            for index, line in enumerate(lines)
            if line.strip().rstrip(':').lower() in heading_set
        ),
        None,
    )
    if start is None:
        return ''

    section_lines = []
    for line in lines[start:]:
        stripped = line.strip().rstrip(':')
        if re.fullmatch(r'[A-Z][A-Z &/]{2,}', stripped):
            break
        section_lines.append(line)
    return '\n'.join(section_lines).strip()


def _extract_known_skills(text: str) -> List[str]:
    lower_text = text.lower()
    return [
        skill for skill in _KNOWN_SKILLS
        if re.search(rf'(?<!\w){re.escape(skill)}(?!\w)', lower_text)
    ]


def _local_resume_parse(raw_text: str) -> Dict:
    lines = [line.strip() for line in raw_text.splitlines() if line.strip()]
    email_match = re.search(r'[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}', raw_text)
    phone_match = re.search(r'(?<!\w)(?:\+?\d{1,3}[ .-]?)?(?:\(?\d{3,5}\)?[ .-]?)?\d{3,5}[ .-]\d{4}(?!\w)', raw_text)
    linkedin_match = re.search(r'(?:https?://)?(?:www\.)?linkedin\.com/in/[^\s|,]+', raw_text, re.I)
    github_match = re.search(r'(?:https?://)?(?:www\.)?github\.com/[^\s|,]+', raw_text, re.I)
    experience_text = _section_text(raw_text, ['experience', 'work experience', 'employment', 'internships'])
    projects_text = _section_text(raw_text, ['projects', 'personal projects', 'academic projects'])
    education_text = _section_text(raw_text, ['education', 'academic background'])
    skills_text = _section_text(raw_text, ['skills', 'technical skills', 'core competencies'])
    summary_text = _section_text(raw_text, ['summary', 'professional summary', 'profile', 'objective', 'about me'])
    skills = _extract_known_skills(f'{skills_text}\n{raw_text}')
    action_verbs = re.findall(
        r'(?im)^\s*(?:[â€¢*\-]|\d+[.)])?\s*(developed|implemented|designed|built|led|managed|optimized|automated|created|deployed|improved|reduced|increased)\b',
        raw_text,
    )
    return _validate_resume_result({
        'name': lines[0] if lines else '',
        'email': email_match.group(0) if email_match else None,
        'phone': phone_match.group(0) if phone_match else None,
        'linkedin': linkedin_match.group(0) if linkedin_match else None,
        'github': github_match.group(0) if github_match else None,
        'professional_summary': summary_text,
        'skills': skills,
        'experience': ([{'job_title': '', 'company': '', 'start_date': '', 'end_date': '', 'duration_months': 0, 'description': experience_text}] if experience_text else []),
        'education': ([{'degree': '', 'institution': '', 'year': '', 'description': education_text}] if education_text else []),
        'certifications': [],
        'projects': ([{'title': '', 'description': projects_text, 'technologies': _extract_known_skills(projects_text)}] if projects_text else []),
        'action_verbs': list(dict.fromkeys(verb.lower() for verb in action_verbs)),
        'keywords': skills,
    })


def _local_jd_parse(raw_text: str) -> Dict:
    skills = _extract_known_skills(raw_text)
    required = []
    preferred = []
    for line in raw_text.splitlines():
        line_skills = _extract_known_skills(line)
        if re.search(r'\b(preferred|bonus|nice to have|plus)\b', line, re.I):
            preferred.extend(line_skills)
        elif re.search(r'\b(required|must have|requirements?|qualifications?)\b', line, re.I):
            required.extend(line_skills)
    return _validate_jd_result({
        'job_title': '',
        'required_skills': list(dict.fromkeys(required)) or skills,
        'preferred_skills': list(dict.fromkeys(preferred)),
        'experience_required': '',
        'education_required': '',
        'key_responsibilities': [],
        'keywords': skills,
    })


def parse_resume(raw_text: str) -> Dict:
    try:
        return _parse_resume_with_groq(raw_text)
    except Exception as exc:
        logger.warning('Groq resume parsing unavailable; using local extraction: %s', exc)
        return _local_resume_parse(raw_text)


def parse_job_description(raw_text: str) -> Dict:
    try:
        return _parse_job_description_with_groq(raw_text)
    except Exception as exc:
        logger.warning('Groq JD parsing unavailable; using local extraction: %s', exc)
        return _local_jd_parse(raw_text)

#it will make sure, that the parse json has all the valid fields we expect
def _validate_jd_result(result: dict) -> dict:
    
    defaults = {
        "job_title": "",
        "required_skills": [],
        "preferred_skills": [],
        "experience_required": "",
        "education_required": "",
        "key_responsibilities": [],
        "keywords": [],
    }

    for key, default in defaults.items():
        if key not in result or result[key] is None:
            result[key] = default
        if isinstance(default, list) and not isinstance(result[key], list):
            result[key] = default

    return result


#to make sure the parse json has all the valid json fields
def _validate_resume_result(result: dict) -> dict:

    defaults = {
        "name": "",
        "email": None,
        "phone": None,
        "linkedin": None,
        "github": None,
        "professional_summary": "",
        "skills": [],
        "experience": [],
        "education": [],
        "certifications": [],
        "projects": [],
        "action_verbs": [],
        "keywords": [],
    }
    for key, default in defaults.items():
        if key not in result or result[key] is None:
            result[key] = default
            
        # Ensure list fields are actually lists
        if isinstance(default, list) and not isinstance(result[key], list):
            result[key] = default

    #Validate experience entries
    for exp in result.get("experience", []):
        if not isinstance(exp, dict):
            continue
        exp.setdefault("job_title", "")
        exp.setdefault("company", "")
        exp.setdefault("start_date", "")
        exp.setdefault("end_date", "")
        exp.setdefault("duration_months", 0)
        exp.setdefault("description", "")
        #Ensure duration_months is an int
        try:
            exp["duration_months"] = int(exp["duration_months"])
        except (ValueError, TypeError):
            exp["duration_months"] = 0

    #Validate project entries
    for proj in result.get("projects", []):
        if not isinstance(proj, dict):
            continue
        proj.setdefault("title", "")
        proj.setdefault("description", "")
        proj.setdefault("technologies", [])

    return result


