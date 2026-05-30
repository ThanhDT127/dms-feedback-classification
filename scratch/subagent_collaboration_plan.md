# DMS Advanced Prompt Engineering: Deep Analysis & Sub-Agent Collaboration Plan

This document outlines the deep architectural research for upgrading the DMS Feedback Classification Prompt and details the cooperative multi-agent division of labor.

---

## 1. Deep Architectural Analysis of Prompt Upgrades

Through our research of the real-world SharePoint data cache, we identified three core areas where the prompt requires deep engineering:

### A. The Autoregressive JSON CoT Trap & The Inversion Fix
* **The Problem:** In autoregressive models like Gemini, token generation proceeds left-to-right. In the legacy JSON structure, the `"labels"` boolean dictionary is generated *before* the `"decision_log"` array. This forces the model to predict all boolean labels *before* it has formulated its logic, leading to frequent over-classification, hallucinated categories, and illogical labeling.
* **The Solution (Inverted JSON Schema):** We will restructure the JSON output so that the LLM generates its reasoning fields *first*, and the final `labels` dictionary at the very end of the JSON object. 

```
[Legacy Flow: Guessing first]
Token stream: ... "labels": { "Báo lỗi": true } ... "decision_log": [ "Cụm từ 'tin thưởng' là ..." ]

[Upgraded Flow: CoT first, Decisions last]
Token stream: ... "decision_log": [ "Cụm từ 'tin thưởng' thực chất là viết sai của 'tin tưởng'..." ] ... "labels": { "Trả thưởng": false, "Tin trung lập": true }
```

### B. Vietnamese Sales Spell Guard & Acronym Glossary
* **The Problem:** Distributors and dealers frequently write shorthand or use dialect spelling variations (e.g. `tin thưởng` for `tin tưởng` / trust, or acronyms like `bh` for Bảo hành, `sp` for Sản phẩm, `at` for Aptomat). Simple string matching or uncalibrated LLM prompts trigger the wrong labels (e.g. `tin thưởng` triggers `Trả thưởng` incorrectly).
* **The Solution:** Embed a formal Vietnamese Sales Term Glossary in the prompt system instructions, with strict negative rules (typo guards) to prevent wrong triggers.

### C. Strict Semantic Exclusion Boundaries
We will explicitly define mutually exclusive categories to prevent fuzzy labeling:
* **`Báo lỗi` vs `Y/c cải tiến`**: `Báo lỗi` is strictly for actual physical failures (burnt out, electrical leak, crack). `Y/c cải tiến` is for design, aesthetics, dimensions, and materials of *existing* products.
* **`Bảo hành` vs `Báo lỗi`**: `Báo lỗi` is about product hardware. `Bảo hành` is about the warranty *service* and *process* speed.
* **`HTPP` vs `Hàng hoá`**: `HTPP` is for agent channel conflicts and price cutting. `Hàng hoá` is for shipping delays, stock levels, and physical packaging in transport.

---

## 2. Multi-Agent Team Structure (The 6-Agent Collaboration)

To manage this complex context, we have divided the work among **6 specialized sub-agents** defined inside the system:

```
                  ┌─────────────────────────────────────┐
                  │            Parent Agent             │
                  │       (Coordinator / Router)        │
                  └──────────────────┬──────────────────┘
                                     │
         ┌───────────────────────────┼───────────────────────────┐
         ▼                           ▼                           ▼
┌─────────────────┐         ┌─────────────────┐         ┌─────────────────┐
│ acronym_        │         │ semantic_       │         │ prompt_schema_  │
│ glossary_expert │         │ boundary_       │         │ architect       │
│                 │         │ specifier       │         │                 │
│ Scans data &    │         │ Defines rules & │         │ Designs CoT &   │
│ builds lookup   │         │ exclusions      │         │ prompt template │
└────────┬────────┘         └────────┬────────┘         └────────┬────────┘
         │                           │                           │
         └───────────────────────────┼───────────────────────────┘
                                     ▼
                        ┌───────────────────────────┐
                        │    few_shot_curator       │
                        │                           │
                        │  Selects 4 real examples  │
                        │  & formats inverted JSON  │
                        └────────────┬──────────────┘
                                     │
         ┌───────────────────────────┴───────────────────────────┐
         ▼                                                       ▼
┌─────────────────┐                                     ┌─────────────────┐
│ quality_        │                                     │ performance_    │
│ assurance_      │                                     │ auditor         │
│ scaffolder      │                                     │                 │
│ Writes robust   │                                     │ Runs validation │
│ pipeline tests  │                                     │ & audit reports │
└─────────────────┘                                     └─────────────────┘
```

### 1. `acronym_glossary_expert` (Glossary Harvester)
* **Goal:** Scans downloaded SharePoint Excel files in `d:\Works\DMS\scratch\sharepoint_cache\Output`, maps Vietnamese sales acronyms/typos, and compiles a definitive lookup table/glossary.
* **Outputs:** `scratch/vietnamese_sales_glossary.json`

### 2. `semantic_boundary_specifier` (Boundary Specifier)
* **Goal:** Defines logic and exclusion rules for confusing category boundaries (e.g. Bug vs Improvement, Warranty service vs Hardware defect).
* **Outputs:** `scratch/semantic_boundaries.md`

### 3. `prompt_schema_architect` (Prompt Architect)
* **Goal:** Designs the inverted JSON CoT format and assembles the updated system prompt template in `issue_classifier.py`.
* **Outputs:** Core template modifications in `issue_classifier.py`

### 4. `few_shot_curator` (Few-Shot Curator)
* **Goal:** Selects 4 high-quality Vietnamese dealer feedback comments from the downloaded data representing tricky cases, and formats them into standard inverted JSON few-shots.
* **Outputs:** `scratch/calibrated_few_shots.json`

### 5. `quality_assurance_scaffolder` (QA Scaffolder)
* **Goal:** Writes extensive test assertions in `tests/test_pipeline.py` using these real-world edge-case comments to verify correct classification.
* **Outputs:** Test suites in `tests/test_pipeline.py`

### 6. `performance_auditor` (Performance Auditor)
* **Goal:** Runs the pipeline on validation slices of downloaded spreadsheets, computes accuracy, and generates a macro-accuracy report.
* **Outputs:** `scratch/macro_accuracy_audit_report.md`

---

## 3. Collaboration & Execution Playbook

Once the user approves the implementation plan, the Parent Agent will coordinate the team using the following phased pipeline:

1. **Phase 1: Knowledge Gathering (Parallel)**
   * Parent invokes `acronym_glossary_expert` and `semantic_boundary_specifier` to harvest vocabulary and boundary exclusions.
2. **Phase 2: Prompt Construction (Sequential)**
   * Parent passes these findings to `few_shot_curator` and `prompt_schema_architect` to build the new prompt template in `issue_classifier.py` with inverted JSON.
3. **Phase 3: QA Scaffolding (Parallel)**
   * Parent invokes `quality_assurance_scaffolder` to implement regression tests for the edge cases.
4. **Phase 4: Auditing & Verification**
   * Parent invokes `performance_auditor` to validate the new prompt on actual downloaded sheets and produce a final macro-accuracy audit report.
