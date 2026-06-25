-- === Call Records Monthly Table Column Enhancement ===
-- Add sentiment and resolution tracking columns to service_churn_call_records_monthly
-- Supports backfill of historical data

-- Add ClientSentiment column if it doesn't exist
IF NOT EXISTS (SELECT 1 FROM sys.columns WHERE object_id = OBJECT_ID('dbo.service_churn_call_records_monthly') AND name = 'ClientSentiment')
    ALTER TABLE dbo.service_churn_call_records_monthly
    ADD ClientSentiment VARCHAR(50) DEFAULT 'UNKNOWN';

-- Add IsResolved column if it doesn't exist
IF NOT EXISTS (SELECT 1 FROM sys.columns WHERE object_id = OBJECT_ID('dbo.service_churn_call_records_monthly') AND name = 'IsResolved')
    ALTER TABLE dbo.service_churn_call_records_monthly
    ADD IsResolved BIT DEFAULT 0;

-- Create indexes on new columns for filtering/sorting performance
IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name='IX_call_records_sentiment' AND object_id=OBJECT_ID('dbo.service_churn_call_records_monthly'))
    CREATE NONCLUSTERED INDEX IX_call_records_sentiment
    ON dbo.service_churn_call_records_monthly (ClientSentiment, MonthStart DESC)
    INCLUDE (CustomerAccount, SubscriberAccount, IsResolved);

IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name='IX_call_records_resolved' AND object_id=OBJECT_ID('dbo.service_churn_call_records_monthly'))
    CREATE NONCLUSTERED INDEX IX_call_records_resolved
    ON dbo.service_churn_call_records_monthly (IsResolved, MonthStart DESC)
    INCLUDE (CustomerAccount, SubscriberAccount, ClientSentiment);

-- Update statistics
UPDATE STATISTICS dbo.service_churn_call_records_monthly WITH FULLSCAN;
