# AI-Powered E-Commerce Analytics Platform
## Project Architecture & Design Decisions

> **Status:** Architecture baseline / POC planning document  
> **Last updated:** 2026-09-11

---

## 1. Project Overview

### 1.1 Vision

Build an AI-powered, schema-independent e-commerce analytics platform that allows a business to:

1. Create an analytics workspace/project.
2. Upload CSV/Excel files or, later, connect databases/e-commerce platforms.
3. Automatically profile and understand unfamiliar datasets.
4. Infer a canonical semantic model and relationships between datasets.
5. Run deterministic business analytics.
6. Automatically generate useful dashboards.
7. Ask business questions in natural language.
8. Receive verified numerical answers, visualizations, and AI-generated explanations.
9. Optionally consume the same analytics model through Power BI.

The key differentiator is **schema adaptability**: the platform should not require every business to use the same column names or database schema.

For example, different businesses may have:

```text
Business A:
order_value
customer_id
purchase_date

Business B:
net_amt
client
txn_dt
```

The platform should infer that these represent equivalent business concepts:

```text
revenue
customer_id
transaction_date
```

---

## 2. Core Design Philosophy

The project follows several principles.

### 2.1 LLMs understand; deterministic systems calculate

The LLM should be responsible for:

- understanding user intent
- interpreting unfamiliar schemas
- mapping fields to semantic concepts
- selecting an analytical approach
- generating explanations
- planning dashboards

The LLM should **not** be the source of truth for arithmetic.

For example:

```text
User:
"How much did revenue grow last quarter?"

LLM:
Determine the requested metric and time comparison.

Analytics engine:
Calculate the actual revenue values.

LLM:
Explain the verified result.
```

This reduces hallucination and makes numerical answers reproducible.

---

### 2.2 Semantic model as the central abstraction

The system should not make every downstream component reason directly about arbitrary source columns.

Instead:

```text
Raw Source Schema
       ↓
Schema Discovery
       ↓
Canonical Semantic Model
       ↓
Analytics / Dashboard / Chatbot / Power BI
```

Once the semantic model exists, downstream components can operate on concepts such as:

```text
revenue
order
customer
product
order_date
category
region
```

rather than worrying about whether the source column was called:

```text
net_amt
sales_value
order_total
amount_paid
```

---

### 2.3 Structured intermediate representations over free-form generation

The system should avoid asking an LLM to directly generate arbitrary application code.

For analytics, the preferred flow is:

```text
Natural Language
      ↓
LLM Planner
      ↓
Analytical Plan / DSL
      ↓
Deterministic Query Compiler
      ↓
Validated SQL
      ↓
Database
```

Similarly, dashboard generation should use:

```text
Dashboard Planner
      ↓
Dashboard Specification / DSL
      ↓
Deterministic Renderer
```

rather than:

```text
LLM → arbitrary React code
```

This makes the system easier to validate, secure, test, and extend.

---

# 3. High-Level Architecture

```text
                         ┌──────────────────────┐
                         │    React Frontend    │
                         │                      │
                         │ Auth / Upload /      │
                         │ Dashboard / Chat     │
                         └──────────┬───────────┘
                                    │ REST / JSON
                                    ▼
                         ┌──────────────────────┐
                         │      FastAPI API     │
                         │                      │
                         │ Auth                  │
                         │ Projects              │
                         │ Data Sources          │
                         │ Analytics             │
                         │ Chat                  │
                         │ Dashboards            │
                         └──────────┬───────────┘
                                    │
             ┌──────────────────────┼──────────────────────┐
             │                      │                      │
             ▼                      ▼                      ▼
    ┌────────────────┐    ┌────────────────┐    ┌────────────────┐
    │ Analytics      │    │ AI / LangChain │    │ Background     │
    │ Service        │    │ Service        │    │ Workers        │
    │                │    │                │    │                │
    │ Metrics        │    │ Schema Mapping │    │ Profiling      │
    │ SQL Compiler   │    │ Planning       │    │ ETL            │
    │ Validation     │    │ Dashboard Plan │    │ Long Jobs      │
    └───────┬────────┘    └────────────────┘    └───────┬────────┘
            │                                             │
            └────────────────────┬────────────────────────┘
                                 ▼
                       ┌──────────────────────┐
                       │     PostgreSQL       │
                       │                      │
                       │ Users                │
                       │ Organizations        │
                       │ Projects             │
                       │ Semantic Model       │
                       │ Metrics              │
                       │ Analytics Results    │
                       │ Dashboards           │
                       │ Queries / Audit      │
                       └──────────┬───────────┘
                                  │
                                  ↕
                       ┌──────────────────────┐
                       │        Redis         │
                       │                      │
                       │ Cache                │
                       │ Rate Limiting        │
                       │ Job Coordination     │
                       └──────────────────────┘

              External / Future Integrations
                       │
              ┌────────┴─────────┐
              ▼                  ▼
       Object Storage         Power BI
       Raw CSV / Excel        Dashboards
```

---

# 4. Technology Stack

## Backend

- **Python**
- **FastAPI**
- SQLAlchemy / equivalent ORM
- Pandas / Polars for profiling and data processing
- PostgreSQL
- Redis
- Background workers such as Celery/RQ

## AI

- LangChain
- LLM provider abstraction
- Structured outputs / JSON schemas
- RAG for analytical patterns where useful

## Frontend

- React
- TypeScript
- Charting library
- Dashboard component system

## BI

- Power BI

Power BI is treated as a downstream visualization/integration layer rather than the core intelligence layer.

## Storage

- PostgreSQL for application metadata, semantic models, metrics, configurations, and analytics state.
- Object storage for large raw CSV/Excel files where appropriate.

---

# 5. Multi-Tenant Application Model

The application is designed as a SaaS-style multi-tenant system.

Core hierarchy:

```text
User
  ↓
Organization / Workspace
  ↓
Analytics Project
  ↓
Data Sources / Datasets
  ↓
Semantic Model
  ↓
Metrics / Analytics / Dashboards / Queries
```

### Core entities

```text
users
organizations
organization_members
analytics_projects
datasets
dataset_columns
dataset_relationships
semantic_models
semantic_fields
metrics
metric_results
dashboards
dashboard_configs
insights
queries
query_results
api_usage
audit_logs
```

Every tenant-owned object should be scoped to its organization/project.

---

# 6. Data Ingestion Architecture

## 6.1 Initial onboarding

The MVP should prioritize:

```text
CSV / Excel upload
```

Later sources:

```text
PostgreSQL
MySQL
Shopify
WooCommerce
Other APIs / connectors
```

---

## 6.2 Upload flow

```text
User
 ↓
React Upload UI
 ↓
FastAPI
 ↓
Object Storage
 ↓
Background Job
 ↓
File Parser
 ↓
Data Profiler
 ↓
Schema Discovery
 ↓
Relationship Detection
 ↓
Semantic Mapping
 ↓
User Confirmation
 ↓
Reference / Semantic Model
```

Long-running processing should not block the HTTP request.

---

# 7. POC Roadmap

The implementation will be intentionally incremental.

## POC 1 — Schema Discovery + Relationship Detection

Goal:

> Given arbitrary CSV/Excel files, determine what the data represents and how datasets relate.

### Input

Example:

```text
orders.csv
customers.csv
products.csv
```

with arbitrary column names.

### Pipeline

```text
Files
 ↓
Parsing
 ↓
Data Profiling
 ↓
Column Type Inference
 ↓
Semantic Field Inference
 ↓
Relationship Detection
 ↓
Canonical Semantic Model
```

### Data profiling

The profiler should extract metadata such as:

- column name
- inferred data type
- null percentage
- unique count
- cardinality
- min/max
- sample values
- possible identifier characteristics
- date characteristics
- categorical characteristics
- numerical characteristics

The LLM should receive useful metadata rather than blindly receiving entire datasets.

### Semantic mapping

Example:

```json
{
  "net_amt": {
    "semantic_type": "revenue",
    "confidence": 0.96
  },
  "purchase_dt": {
    "semantic_type": "transaction_date",
    "confidence": 0.98
  },
  "client": {
    "semantic_type": "customer_id",
    "confidence": 0.91
  }
}
```

Low-confidence mappings should be surfaced for user confirmation.

### Relationship detection

Example:

```text
orders.customer_id
        │
        ▼
customers.customer_id
```

and:

```text
orders.product_id
        │
        ▼
products.product_id
```

Relationship detection can use:

- column-name similarity
- data type compatibility
- uniqueness
- value overlap
- cardinality
- semantic meaning
- LLM reasoning as a supporting signal

The final relationship should be validated rather than blindly trusting an LLM.

---

# 8. Canonical Semantic / Reference Model

The project needs a stable internal representation that downstream components understand.

A possible e-commerce model:

```text
Customer
 ├── customer_id
 ├── name
 ├── region
 └── signup_date

Order
 ├── order_id
 ├── customer_id
 ├── order_date
 ├── revenue
 └── product_id

Product
 ├── product_id
 ├── product_name
 ├── category
 └── price
```

The exact physical database design can evolve during POC 1.

Important distinction:

```text
Raw Data
```

and:

```text
Semantic Model
```

are separate concepts.

The semantic model describes what fields mean and how they relate. Raw/staged data can live separately.

---

# 9. POC 2 — Analytics Engine

Once the reference/semantic model exists, build a deterministic analytics engine.

## 9.1 Core metrics

Initial e-commerce metrics:

- Revenue
- Orders
- Customers
- Average Order Value (AOV)
- Repeat Purchase Rate
- Revenue Growth
- Order Growth
- Customer Growth
- Product/category performance
- Regional performance

Later:

- Customer Lifetime Value
- Cohort retention
- RFM segmentation
- Conversion rate
- Moving averages
- Anomaly detection
- Correlation
- Statistical tests

---

## 9.2 Metric registry

Metrics should have canonical definitions.

Example:

```text
Revenue
= SUM(order.revenue)
```

```text
Orders
= COUNT(DISTINCT order.order_id)
```

```text
Customers
= COUNT(DISTINCT order.customer_id)
```

```text
AOV
= Revenue / Orders
```

This registry becomes a source of truth for the system.

The LLM should reference registered metrics rather than inventing formulas.

---

# 10. Analytical DSL / AST

The preferred intermediate representation is a structured analytical plan.

Example:

```json
{
  "operation": "group_by",
  "metric": {
    "name": "revenue",
    "aggregation": "sum"
  },
  "dimension": "category",
  "time_filter": {
    "range": "last_6_months"
  },
  "sort": {
    "field": "revenue",
    "direction": "desc"
  }
}
```

The system then compiles this into SQL.

```text
LLM
 ↓
Analytical AST
 ↓
Semantic Validation
 ↓
SQL Compiler
 ↓
SQL
```

This separates reasoning from execution.

---

# 11. SQL Generation Strategy

The architecture will use a hybrid approach.

## 11.1 Common queries

Use deterministic templates/query patterns.

Example:

```sql
SELECT
    category,
    SUM(revenue) AS revenue
FROM orders
WHERE order_date BETWEEN :start_date AND :end_date
GROUP BY category
ORDER BY revenue DESC;
```

The actual table/column mapping comes from the semantic model.

---

## 11.2 Long-tail queries

For novel analytical questions:

```text
User Question
 ↓
LLM Planner
 ↓
Analytical AST
 ↓
SQL Generation / Compilation
 ↓
SQL Validation
 ↓
Read-only Database
```

Few-shot examples and RAG can improve planning and long-tail question handling.

---

# 12. SQL Safety

The system must never blindly execute arbitrary LLM-generated SQL.

Validation should enforce:

- SELECT/read-only operations
- allowed tables
- allowed columns
- tenant isolation
- query complexity limits
- reasonable row limits
- parameterized values
- prevention of destructive statements
- timeout controls

Database credentials used by the analytics layer should be read-only where possible.

---

# 13. POC 3 — Dashboard Generation

The dashboard generator should convert semantic and analytical information into a structured dashboard specification.

Example:

```json
{
  "dashboard": {
    "title": "Executive Sales Overview",
    "filters": [
      "date",
      "region",
      "category"
    ],
    "components": [
      {
        "type": "kpi",
        "metric": "revenue"
      },
      {
        "type": "kpi",
        "metric": "orders"
      },
      {
        "type": "kpi",
        "metric": "aov"
      },
      {
        "type": "line_chart",
        "metric": "revenue",
        "dimension": "date"
      },
      {
        "type": "bar_chart",
        "metric": "revenue",
        "dimension": "category"
      },
      {
        "type": "bar_chart",
        "metric": "revenue",
        "dimension": "region"
      }
    ]
  }
}
```

---

## 13.1 Dashboard DSL

Initial component types:

```text
KPI
LINE_CHART
BAR_CHART
PIE_CHART
TABLE
HEATMAP
FUNNEL
COHORT
SCATTER
```

The frontend contains deterministic renderers for these components.

```text
Dashboard JSON
      ↓
React Renderer
      ↓
Interactive Dashboard
```

---

## 13.2 Data-aware dashboard generation

The dashboard planner should consider:

- available metrics
- available dimensions
- business type
- trends
- anomalies
- important segments
- data quality
- user goals

Therefore dashboards should not merely be generic collections of charts.

Example:

```text
Revenue declining
        ↓
Dashboard planner may emphasize:
- revenue trend
- category contribution
- regional contribution
- top/bottom products
```

If retention is especially problematic:

```text
Dashboard planner
        ↓
Prioritize:
- repeat purchase rate
- cohort retention
- customer segments
```

---

# 14. Power BI Integration

Power BI is an important target integration but should not dictate the core architecture.

The same semantic model should ideally support:

```text
Semantic Model
      ├──────────────► Native React Dashboard
      │
      └──────────────► Power BI
```

The system should avoid making Power BI the source of business definitions.

Metrics and semantic mappings remain controlled by the platform.

Power BI programmatic report creation, embedding, authentication, and licensing constraints will be investigated during the integration phase rather than assumed upfront.

---

# 15. POC 4 — Natural Language Analytics Chatbot

The chatbot architecture:

```text
User Question
      ↓
Intent Understanding
      ↓
Semantic Model Lookup
      ↓
Analytical Plan / AST
      ↓
Known Query Pattern?
   ↙              ↘
 Yes               No
 ↓                  ↓
Template          Novel SQL
 ↓                  ↓
 └──────────┬───────┘
            ↓
      SQL Validation
            ↓
       Read-only DB
            ↓
      Result Validation
            ↓
     Verified Result
            ↓
      Insight LLM
            ↓
Answer + Chart + Explanation
```

Example:

> "Which category caused the revenue decline last quarter?"

The system should:

1. Resolve `revenue` using the metric registry.
2. Resolve the time period.
3. Compare relevant periods.
4. Group revenue by category.
5. Determine contribution to the decline.
6. Return the actual values.
7. Ask the LLM to explain the result.

The LLM should not independently invent the numerical answer.

---

# 16. Handling Unsupported Questions

The system should explicitly detect when the data cannot answer a question.

Example:

```text
User:
"What is our website conversion rate?"
```

If there is no visitor/session data:

```text
Cannot calculate conversion rate because the connected
dataset does not contain the required visitor/session data.
```

The system should prefer an explicit limitation over hallucination.

---

# 17. AI Architecture

LangChain is used as the orchestration layer around LLM functionality.

Potential AI modules:

```text
Schema Mapper
Relationship Analyzer
Intent Planner
Analytical Planner
Dashboard Planner
Insight Generator
```

These should not become uncontrolled autonomous agents.

Prefer:

```text
Typed input
   ↓
LLM
   ↓
Structured output
   ↓
Deterministic validation
   ↓
Next system component
```

---

# 18. RAG Strategy

RAG should be used where it provides value, not as a default for every operation.

Potential retrieval corpus:

- analytical query patterns
- metric definitions
- semantic mapping examples
- business terminology
- dashboard patterns
- few-shot examples

For example:

```text
Question:
"Show me sales by region"

Retriever:
revenue_by_dimension pattern

LLM:
produce structured analytical plan

Compiler:
generate deterministic SQL
```

The semantic model itself should remain the authoritative source for the connected dataset.

---

# 19. Backend Service Boundaries

A possible FastAPI service structure:

```text
app/
├── api/
│   ├── auth/
│   ├── organizations/
│   ├── projects/
│   ├── datasets/
│   ├── analytics/
│   ├── dashboards/
│   └── chat/
│
├── services/
│   ├── ingestion/
│   ├── profiling/
│   ├── schema/
│   ├── relationships/
│   ├── semantic/
│   ├── analytics/
│   ├── dashboard/
│   └── ai/
│
├── models/
├── repositories/
├── workers/
├── security/
├── validation/
└── core/
```

The exact directory structure can evolve; service boundaries are more important than folder naming.

---

# 20. Background Processing

The following tasks should be asynchronous:

- large file parsing
- data profiling
- schema discovery
- relationship detection
- data transformation
- analytics materialization
- dashboard generation
- large report generation

Example:

```text
POST /datasets/upload
        ↓
Create dataset record
        ↓
Queue job
        ↓
Return job ID
        ↓
Worker processes dataset
        ↓
Update dataset status
```

Possible states:

```text
UPLOADED
PROCESSING
PROFILED
MAPPED
AWAITING_CONFIRMATION
READY
FAILED
```

---

# 21. Redis Usage

Redis should be used selectively.

Primary uses:

### Caching

Cache expensive/repeated analytics queries:

```text
semantic_model + query + filters
                ↓
             cache key
                ↓
             result
```

### Rate limiting

Protect:

- authentication endpoints
- file uploads
- LLM calls
- chatbot endpoints
- expensive analytics queries

### Job coordination

Depending on the worker architecture, Redis can also support background-job coordination.

Redis should not become the primary persistent source of truth.

---

# 22. Database Architecture

PostgreSQL is the preferred relational database.

Possible hosted options for development/free-tier deployment include platforms such as Supabase or Neon.

The application database should store:

```text
Identity
Tenant information
Projects
Dataset metadata
Semantic mappings
Relationships
Metric definitions
Dashboard specifications
Query metadata
Insights
Audit information
```

Large raw files should generally live in object storage rather than being stored directly inside PostgreSQL.

---

# 23. Authentication & Authorization

The final application should support:

```text
Sign Up
   ↓
Login
   ↓
Organization
   ↓
Project
```

Authorization should be tenant-aware.

A user should only be able to access:

```text
Organizations they belong to
        ↓
Projects in those organizations
        ↓
Datasets belonging to those projects
```

RBAC can later support roles such as:

```text
Owner
Admin
Analyst
Viewer
```

---

# 24. Security Principles

Security requirements include:

- password/authentication best practices
- tenant isolation
- RBAC
- read-only analytics DB access
- SQL validation
- parameterized queries
- file type validation
- file size limits
- safe parsing
- secrets encryption/management
- API rate limiting
- audit logs
- input validation
- request timeouts
- LLM output validation

Database credentials for external sources should never be stored as plaintext application data.

---

# 25. Observability

The final system should include:

```text
Structured logging
Metrics
Error tracking
LLM usage tracking
Query latency
Background job status
API latency
Cache hit/miss rates
```

Useful operational metrics:

- ingestion success rate
- schema mapping accuracy
- relationship detection accuracy
- query latency
- LLM latency
- token/cost usage
- dashboard generation latency
- cache hit rate
- failed jobs
- SQL validation failures

---

# 26. Evaluation Strategy

Because this is an AI-heavy system, each POC should have measurable evaluation criteria.

## POC 1

Measure:

- schema mapping accuracy
- relationship detection accuracy
- confidence calibration
- user correction rate

Example benchmark:

```text
100 datasets / dataset combinations
        ↓
Expected semantic mappings
        ↓
Expected relationships
        ↓
Compare system output
```

---

## POC 2

Measure:

- numerical correctness
- query correctness
- metric correctness
- execution latency

The same business question should always produce the correct deterministic result.

---

## POC 3

Measure:

- dashboard usefulness
- metric relevance
- chart selection
- absence of unsupported metrics
- layout quality

---

## POC 4

Measure:

- intent accuracy
- analytical-plan accuracy
- SQL execution accuracy
- numerical answer accuracy
- hallucination rate
- latency

---

# 27. Model/Architecture Experiments

The analytics chatbot should eventually be benchmarked across strategies such as:

```text
Template-only
       vs
Few-shot SQL generation
       vs
RAG + few-shot
       vs
Hybrid planner + deterministic compiler
```

This gives the project an empirical engineering component rather than relying purely on subjective claims.

---

# 28. End-to-End Product Flow

The intended final product flow is:

```text
┌──────────────┐
│     User     │
└──────┬───────┘
       ↓
┌──────────────┐
│ Login / Org  │
└──────┬───────┘
       ↓
┌─────────────────────┐
│ Create Analytics    │
│ Project             │
└──────┬──────────────┘
       ↓
┌─────────────────────┐
│ Upload / Connect    │
│ Data                │
└──────┬──────────────┘
       ↓
┌─────────────────────┐
│ Profile Data        │
└──────┬──────────────┘
       ↓
┌─────────────────────┐
│ AI Schema Mapping   │
└──────┬──────────────┘
       ↓
┌─────────────────────┐
│ User Confirmation   │
└──────┬──────────────┘
       ↓
┌─────────────────────┐
│ Semantic Model      │
└──────┬──────────────┘
       ↓
┌─────────────────────┐
│ Analytics Engine    │
└──────┬──────────────┘
       ├───────────────┐
       ↓               ↓
┌──────────────┐  ┌──────────────┐
│ Dashboard    │  │ AI Insights  │
└──────────────┘  └──────────────┘
       │
       ↓
┌─────────────────────┐
│ Natural Language    │
│ Analytics Chat      │
└──────┬──────────────┘
       ↓
┌─────────────────────┐
│ Native Dashboard /  │
│ Power BI            │
└─────────────────────┘
```

---

# 29. Development Order

The project will **not** be built as one large full-stack application from day one.

The agreed development sequence is:

```text
Phase 0
Analytics fundamentals + manual dashboard exercise
        ↓
Phase 1
Schema Discovery + Relationship Detection POC
        ↓
Phase 2
Analytics Engine POC
        ↓
Phase 3
Dashboard Generation POC
        ↓
Phase 4
Natural Language Analytics POC
        ↓
Phase 5
Integrate into FastAPI
        ↓
Phase 6
Authentication + Multi-tenancy
        ↓
Phase 7
Redis + Background Workers + Caching
        ↓
Phase 8
React Frontend
        ↓
Phase 9
Power BI Integration
        ↓
Phase 10
Security + Observability + Deployment
```

This order reduces complexity and ensures every major AI/system component works independently before integration.

---

# 30. POC 1 Immediate Implementation Target

The first actual engineering milestone should be a small but representative e-commerce dataset containing multiple related files.

For example:

```text
orders.csv
customers.csv
products.csv
```

The POC should produce:

```text
1. Data profile
2. Inferred column types
3. Semantic mappings
4. Mapping confidence
5. Candidate relationships
6. Relationship confidence
7. Canonical semantic model
8. Evaluation against known ground truth
```

A useful POC output could look conceptually like:

```text
orders.csv
 ├── order_id       → order.id
 ├── client         → customer.id
 ├── purchase_dt    → order.date
 ├── net_amt        → order.revenue
 └── product_code   → product.id

customers.csv
 ├── client          → customer.id
 ├── customer_region → customer.region
 └── signup_dt       → customer.signup_date

products.csv
 ├── product_code → product.id
 ├── title        → product.name
 └── category_nm  → product.category
```

Relationships:

```text
orders.client
      ↓
customers.client

orders.product_code
      ↓
products.product_code
```

Only after this POC is reliable should the analytics engine be built on top of it.

---

# 31. Major Design Decisions — Summary

| Decision | Choice | Reason |
|---|---|---|
| Core architecture | Schema-independent analytics platform | Supports heterogeneous business datasets |
| Data onboarding | CSV/Excel first | Simplest realistic MVP |
| Future sources | DBs + e-commerce platforms | Extensibility |
| Semantic abstraction | Canonical semantic model | Decouples source schema from analytics |
| LLM role | Understanding/planning/explanation | Reduces hallucination |
| Arithmetic | Deterministic SQL/Python | Numerical correctness |
| Analytics interface | Analytical AST/DSL | Safer than direct free-form SQL |
| Common queries | Templates/query patterns | Reliability + speed |
| Long-tail queries | LLM-assisted planning/SQL | Coverage |
| RAG | Selective | Useful for patterns/definitions, not everything |
| Dashboard generation | Structured dashboard DSL | Deterministic rendering |
| Native dashboard | React | Full control and easier MVP |
| BI integration | Power BI later | Important integration, not core dependency |
| Backend | FastAPI/Python | Fits analytics + AI workload |
| Primary DB | PostgreSQL | Relational/analytics-friendly foundation |
| Raw file storage | Object storage | Avoid large binary data in relational DB |
| Cache | Redis | Fast repeated analytics + rate limiting |
| Async jobs | Worker queue | File/analytics/AI jobs can be long-running |
| Auth | Multi-tenant + RBAC | SaaS architecture |
| SQL security | Read-only + validation | Prevent destructive/unsafe execution |
| Evaluation | Benchmark-driven | Quantify AI system quality |
| Development | POC-first | Validate difficult components before integration |

---

# 32. Non-Goals / Things We Are Intentionally Avoiding

The project should avoid the following architectural traps:

### 32.1 LLM doing raw arithmetic

Avoid:

```text
LLM → "Revenue is $1.23M"
```

without deterministic computation.

Prefer:

```text
SQL → $1.23M
LLM → explanation
```

### 32.2 LLM-generated frontend code

Avoid:

```text
LLM → React source code → execute
```

Prefer:

```text
LLM → Dashboard JSON → trusted renderer
```

### 32.3 Blind SQL execution

Avoid:

```text
LLM → arbitrary SQL → production database
```

Prefer:

```text
LLM → plan → validation → read-only SQL → execution
```

### 32.4 Power BI as the core engine

Power BI should consume the platform's semantic/analytics layer rather than define the entire architecture.

### 32.5 Full-stack-first development

Do not build authentication, Redis, React, Power BI, AI agents, and analytics simultaneously.

The difficult intelligence components are validated first through isolated POCs.

---

# 33. Target End State

The final architecture should resemble:

```text
                 ┌───────────────────────────┐
                 │       React / Web         │
                 │                           │
                 │ Dashboard | Chat | Admin  │
                 └─────────────┬─────────────┘
                               │
                               ▼
                 ┌───────────────────────────┐
                 │          FastAPI          │
                 │                           │
                 │ Auth | Projects | Data   │
                 │ Analytics | Dashboard    │
                 │ Chat | Power BI          │
                 └─────────────┬─────────────┘
                               │
              ┌────────────────┼────────────────┐
              │                │                │
              ▼                ▼                ▼
       ┌────────────┐   ┌────────────┐   ┌────────────┐
       │ Semantic   │   │ Analytics  │   │ AI         │
       │ Layer      │   │ Engine     │   │ Orchestrator│
       └─────┬──────┘   └─────┬──────┘   └─────┬──────┘
             │                │                │
             └────────────────┼────────────────┘
                              ▼
                    ┌──────────────────┐
                    │    PostgreSQL    │
                    └────────┬─────────┘
                             ↕
                    ┌──────────────────┐
                    │      Redis       │
                    └──────────────────┘
                             ↕
                    ┌──────────────────┐
                    │ Background Jobs  │
                    └──────────────────┘
                             │
              ┌──────────────┴──────────────┐
              ▼                             ▼
       Object Storage                    Power BI
       Raw Data                          Integration
```

The central architectural idea is:

> **Convert messy source data into a trusted semantic model once, then make analytics, dashboards, natural-language querying, and BI integrations operate on that shared model.**

That abstraction is the foundation of the entire project.
