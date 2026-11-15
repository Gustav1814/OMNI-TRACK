# Documentation

Start with the main [README](../README.md) at the repo root.

| Document | What it is |
|----------|------------|
| [Remediation plan](REMEDIATION_PLAN.md) | Why the pipeline breaks today, and the 8-week, phase-by-phase fix plan through to submission |
| [Product spec](PRODUCT_SPEC.md) | Pages, KPIs, alerts and roles for store owners and ops teams, how cameras share tracking data, and the demo footage and datasets |
| [Evaluation](EVALUATION.md) | How each proposal success criterion is measured, the datasets used, and the measured results (also shown on the Evaluation page) |
| [FYP-I proposal](proposal/FYP-I-Proposal-OmniTrack-Template.docx) | The original proposal and its success criteria |
| [Database base](dev/DATABASE_ENTERPRISE_BASE.md) | PostgreSQL + pgvector partitioning and retention |
| [Scalability and plugin base](dev/SCALABILITY_AND_PLUGIN_BASE.md) | Extension points and scaling notes |

Demo footage lives in `backend/storage/demo_videos/` (git-ignored). The reference clips' sources and licences are listed in `backend/storage/demo_videos/reference/SOURCES.md`; the CAVIAR and Mall datasets are covered in [EVALUATION.md](EVALUATION.md#datasets).
