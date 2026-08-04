BEGIN;

DO $$
DECLARE
    dup_count INTEGER;
BEGIN
    SELECT COUNT(*) INTO dup_count
    FROM (
        SELECT tenant_id, customer_id
        FROM leads
        WHERE status = 'new'
        GROUP BY tenant_id, customer_id
        HAVING COUNT(*) > 1
    ) dup;
    IF dup_count > 0 THEN
        RAISE EXCEPTION 'Duplicate open leads found: % group(s) with more than one open lead per customer. Manual resolution required before migration 050.', dup_count;
    END IF;

    SELECT COUNT(*) INTO dup_count
    FROM (
        SELECT tenant_id, event_type, aggregate_id
        FROM business_events
        WHERE event_type = 'lead.created'
          AND aggregate_id IS NOT NULL
        GROUP BY tenant_id, event_type, aggregate_id
        HAVING COUNT(*) > 1
    ) dup;
    IF dup_count > 0 THEN
        RAISE EXCEPTION 'Duplicate lead.created events found: % group(s) with more than one event per aggregate. Manual resolution required before migration 050.', dup_count;
    END IF;
END;
$$;

CREATE UNIQUE INDEX IF NOT EXISTS uq_leads_open_per_customer
ON leads (tenant_id, customer_id)
WHERE status = 'new';

CREATE UNIQUE INDEX IF NOT EXISTS uq_business_events_lead_created
ON business_events (tenant_id, event_type, aggregate_id)
WHERE event_type = 'lead.created'
  AND aggregate_id IS NOT NULL;

COMMIT;
