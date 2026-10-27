# Synthetic application scenario

Every candidate detail, the vacancy and “Листва Лаб” are fictional. The email uses the reserved `example.com` domain. These files are safe examples, not real applicant documents and not measured AI evaluation results.

- `resume.pdf`: readable synthetic input PDF, with a visible demonstration label.
- `vacancy.txt`: synthetic backend internship.
- `company.md`: fictional employer profile; no fabricated public URLs.
- `example.json`: prepared application state for the offline demonstration.
- `match.json`: prepared requirement-level findings.
- `interview.txt`: example answers; they are not automatically inserted into a real profile.
- `adapted-resume.md`: editable example after accepting the proposed change.
- `cover-letter.txt`: prepared cover-letter example.

The app creates a fresh copy of this fixture per demo application. Changes to that copy do not edit these source files. “AI” actions use the actual configured provider and never fall back to these answers.

To walk through without API usage: open the match, skip interview, review and accept/reject the change, assemble and confirm the résumé, confirm the letter, then download the application package. Upload `resume.pdf` into a separate new application to exercise actual local PDF extraction.
