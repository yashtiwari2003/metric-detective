# Interview Questions & Answers

**1. Why did you build this?**
I built Metric Detective to automate root-cause analysis for metric drops. In my experience, data teams spend days writing ad-hoc SQL to find why a KPI changed. This tool does the heavy lifting, generating hypotheses, running SQL, and providing an evidence-backed report automatically.

**2. Explain the architecture**
It starts with a **KPI Monitor** (STL/Z-Score anomaly detection). When a drop is found, **Contribution Analysis** breaks down dimensions. The **Hypothesis Agent (LLM)** formulates ideas. The **SQL Agent** generates and safely runs queries. Then, **Statistical Tests** validate significance. Finally, a **Verifier** ensures LLM outputs match data, and the **Reporter** generates the final report.

**3. Why a verifier?**
LLMs hallucinate numbers. If an executive acts on a report, the numbers must be perfect. The verifier checks every metric cited in the LLM's text against the source JSON/SQL results. If they don't match, the report is rejected or regenerated.

**4. How do you measure accuracy?**
I built an evaluation pipeline injecting 30 synthetic anomalies (like a 40% drop in a specific region). I track top-1 accuracy (did it find the primary cause?), top-3 accuracy, and precision (fraction of correct claims) across the evaluation set.

**5. Why DuckDB?**
It requires zero setup, runs fast analytical queries locally, and integrates perfectly with SQLAlchemy, making the code portable to PostgreSQL or Snowflake later.

**6. How do you prevent SQL injection?**
The SQL Agent runs through a `sql_guard` module. It enforces a strict whitelist (only `SELECT`), adds `LIMIT` clauses, and implements timeouts. The LLM only has read access to the designated analytical tables.

**7. What statistical tests do you use?**
Welch's t-test for continuous variables (like average order value), proportion z-test for rates (like conversion or late delivery), and chi-square tests for categorical distributions.

**8. How do you handle LLM failures?**
If the LLM fails to generate a valid SQL query or the verifier rejects the report multiple times, the system falls back to a deterministic **Contribution Analysis** report (dimension breakdown without natural language).

**9. What's the cost per investigation?**
Using models like Gemini Flash/Flash-Lite, the cost per investigation is kept under $0.05, making it highly scalable for daily automated runs.

**10. What would you do differently at scale?**
I would implement real-time streaming detection instead of batch, add query caching, and use a vector store to give the LLM historical context of previous anomalies.
