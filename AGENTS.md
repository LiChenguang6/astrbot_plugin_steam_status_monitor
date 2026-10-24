# Repository maintenance

- User-facing changes must update `metadata.yaml` and `CHANGELOG.md` together before committing/pushing to GitHub. Keep a dated entry with concrete changes, migration impact and verified checks. Do not claim GitHub Releases or hosted CI passed unless verified.
- Run the relevant tests; the standard suite is `python -m pytest -q`, with `node --check pages/word-guardian/app.js` for frontend changes.
- Learning records belong to one person per plan. `user_ids` contains aliases for the same person; never merge independent members' progress or debt. Preserve existing plans and records when improving multi-account binding.
- Do not commit runtime databases, real QQ identities, model credentials or private screenshots. Keep temporary AstrBot runtimes outside the checkout.
