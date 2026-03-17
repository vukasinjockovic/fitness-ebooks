# IMPORTANT: Production DB query filters

When querying public.bp_cpts for --db-source production, ALWAYS include:
- WHERE cpt_name = 'recipes'
- AND status = 'published'
- AND deleted_at IS NULL

This ensures we only use active, published recipes — not drafts, trashed, or archived ones.
