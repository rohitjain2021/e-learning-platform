# Learning program knowledge base demo

## Demo purpose

This demo shows how an organization can help learners and mentors find answers in program documents. The fictional teacher pathway covers onboarding, practice hours, competency evidence, reviews, and progress reporting.

## Demo story

"I built a source-backed assistant for a fictional teacher pathway. A learner or mentor can ask how to submit hours, what evidence is needed, or how a review works. The assistant retrieves the relevant program passages and returns an explanation with source references."

## Five source documents

The files in [data/demo/learning](../data/demo/learning/) are short UTF-8 text documents for one fictional program. All names, rules, targets, review deadlines, and learner records are invented. They do not describe actual organizational policies, program requirements, or legal eligibility criteria.

| File | Purpose |
| --- | --- |
| `01-program-overview.txt` | Program purpose, onboarding, roles, and completion requirements. |
| `02-hours-and-submissions.txt` | Eligible activities, excluded time, and submission rules. |
| `03-competencies-and-evidence.txt` | Four skills and the evidence needed for mentor sign-off. |
| `04-mentor-review-and-escalation.txt` | Review ownership, corrections, and escalation. |
| `05-progress-and-reporting.txt` | One fictional snapshot for answering progress questions. |

Keep this guide outside the knowledge base source prefix. The assistant should retrieve the five program documents, not this answer key. Files have a title, document ID, version, and descriptive filename to help reviewers trace evidence.

## Questions and expected evidence

These are evaluation expectations, not recorded successful application results.

| Question | Expected answer | Source documents |
| --- | --- | --- |
| What must I do before I can log classroom hours? | Accept the invitation, check the assignment, finish orientation, and wait for placement activation. | 01 |
| Can I include online coursework in practice hours? | No; online coursework is excluded from the practice target. | 02 |
| When do I submit weekly hours? | Friday, 5 p.m., in the placement's local time. | 02 |
| Does uploading evidence complete a competency? | No; explicit mentor sign-off is required. | 03 |
| What evidence is needed for Checking Understanding? | An anonymized assessment example, brief reflection, and mentor observation. | 03 |
| My hours have been pending for four business days. What do I do? | Escalate with the submission ID to the administrator; no automatic approval. | 02, 04 |
| Can a learner approve their own hours? | No; the assigned mentor or an authorized replacement reviews them. | 04 |
| How many hours does DEMO-001 still need? | In the week-9 snapshot, 60 approved hours remain; pending hours are excluded. | 05 |
| What if all 20 pending hours are approved? | Hypothetically, 200 approved hours and 40 remaining. Do not claim approval occurred. | 05 |
| Has DEMO-001 completed the program? | No: hours remain, C4 lacks sign-off, and final administrator review is incomplete. | 01, 03, 05 |
| Does the program provide financial support to learners? | The source documents do not specify financial support; do not invent an amount. | No answer in the corpus |
| Does program completion give me a teaching license? | This fictional program does not establish a teaching license. | 01, 05 |

For each supported answer, verify that the cited passage actually supports the statement. A filename alone is not evidence that the answer is correct. For private S3 content, a source URI is an identifier; browser access requires authorized retrieval or a short-lived signed link generated for an authorized user.

The progress snapshot demonstrates retrieval and a small calculation over fixed text. Live progress reports and aggregate questions belong against an authorized operational datastore, with deterministic calculations rather than treating a document search as a live database.

## Architecture and operations

- **Business value:** Reduce time spent searching program guides and answering repeated procedural questions. Measure answer usefulness and time saved rather than assuming an improvement.
- **Deployment:** Explain Serverless Framework generating CloudFormation, GitHub Actions CI, and a future deployment workflow using GitHub OIDC. The current CI checks dependencies; it does not deploy the stack.
- **Storage:** Demonstrate the deployed S3 bucket's encryption, blocked public access, and versioning. Separate application runtime access from deployment permissions.
- **Authorization:** This sample corpus is intentionally shared across demo users. A multi-organization service needs tenant and role checks before retrieval and before serving source links. A model prompt alone does not enforce authorization.
- **Quality:** Use the question table as a small evaluation set. Measure retrieval relevance, answer correctness, citation support, and appropriate handling of missing information. Record real results only after running the application.
- **Failure behavior:** Discuss empty retrieval, unavailable models, throttling, timeouts, bounded retries, and an expired source link. Do not silently answer without evidence when the program documents are insufficient.
- **Operations:** Track request latency, retrieval and model errors, token usage, and cost per request. Avoid recording sensitive document contents in routine logs. Add request IDs for tracing.
- **Change management:** Update a source document, ingest the change, and rerun affected questions. Object versioning alone does not refresh a knowledge base; ingestion and evaluation are separate steps.

## Current boundary

The five source documents are stored under `s3://e-learning-platform-knowledge-base/demo/learning/`. This guide stays outside the source prefix so its expected answers are not ingested. Connecting and synchronizing a Bedrock data source, implementing the assistant, and running the evaluation are separate actions.
