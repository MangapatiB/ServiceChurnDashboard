-- === Dashboard Query Optimization Indexes ===
-- Run this once to index the key dashboard lookup tables.
-- Expected impact: 10,000 account load time from ~240s to ~30-60s

-- 1. Truckroll table: Speed up city filter and top-account selection
IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name='IX_truckroll_city_subscriber' AND object_id=OBJECT_ID('dbo.service_churn_truckroll_base'))
  CREATE NONCLUSTERED INDEX IX_truckroll_city_subscriber
  ON dbo.service_churn_truckroll_base (BillingCity, SubscriberAccountNumber)
  INCLUDE (LegacyAccountNumber, PhoneNumber)
  WHERE SubscriberAccountNumber IS NOT NULL;

-- 2. Residential churn: Speed up subscriber account lookup and churn probability sort
IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name='IX_res_churn_subscriber' AND object_id=OBJECT_ID('dbo.service_churn_res_latest'))
  CREATE NONCLUSTERED INDEX IX_res_churn_subscriber
  ON dbo.service_churn_res_latest (SubscriberAccountNumber, ChurnProbability DESC)
  INCLUDE (PredictionMonth, Top1Feature, Top2Feature, Top3Feature);

-- 3. Commercial churn: Same as residential for commercial segment
IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name='IX_com_churn_subscriber' AND object_id=OBJECT_ID('dbo.service_churn_com_latest'))
  CREATE NONCLUSTERED INDEX IX_com_churn_subscriber
  ON dbo.service_churn_com_latest (SubscriberAccountNumber, ChurnProbability DESC)
  INCLUDE (PredictionMonth, Top1Feature, Top2Feature, Top3Feature);

-- 4. Call monthly aggregates: Speed up customer type + subscriber/account lookup
IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name='IX_call_monthly_type_subscriber' AND object_id=OBJECT_ID('dbo.service_churn_call_monthly_agg'))
  CREATE NONCLUSTERED INDEX IX_call_monthly_type_subscriber
  ON dbo.service_churn_call_monthly_agg (CustomerType, SubscriberAccount)
  INCLUDE (AccountNumber, NumberOfCalls, AverageAgentTalkMin, TotalAgentTalkMin);

IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name='IX_call_monthly_type_account' AND object_id=OBJECT_ID('dbo.service_churn_call_monthly_agg'))
  CREATE NONCLUSTERED INDEX IX_call_monthly_type_account
  ON dbo.service_churn_call_monthly_agg (CustomerType, AccountNumber)
  INCLUDE (SubscriberAccount, NumberOfCalls);

-- 5. Account MAC mapping: Speed up account number lookup
IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name='IX_account_mac_account' AND object_id=OBJECT_ID('dbo.service_churn_account_mac_map'))
  CREATE NONCLUSTERED INDEX IX_account_mac_account
  ON dbo.service_churn_account_mac_map (AccountNumber)
  INCLUDE (ModemMac);

-- 6. Modem health: Speed up modem MAC lookup with all critical fields
IF NOT EXISTS (SELECT 1 FROM sys.indexes WHERE name='IX_modem_health_mac' AND object_id=OBJECT_ID('dbo.service_churn_modem_health_latest'))
  CREATE NONCLUSTERED INDEX IX_modem_health_mac
  ON dbo.service_churn_modem_health_latest (ModemMac)
  INCLUDE (IP, LastSeen, Status, State, USRXLVL, USTXPWR, USRXSNR, DSRXLVL, DSRXSNR, DSPREFEC, DSPOSTFEC, DSBW, USBW, FiberNode, CMTS);

-- === Index validation ===
-- Update statistics after creation for optimal query plans
UPDATE STATISTICS dbo.service_churn_truckroll_base WITH FULLSCAN;
UPDATE STATISTICS dbo.service_churn_res_latest WITH FULLSCAN;
UPDATE STATISTICS dbo.service_churn_com_latest WITH FULLSCAN;
UPDATE STATISTICS dbo.service_churn_call_monthly_agg WITH FULLSCAN;
UPDATE STATISTICS dbo.service_churn_account_mac_map WITH FULLSCAN;
UPDATE STATISTICS dbo.service_churn_modem_health_latest WITH FULLSCAN;

-- === Verify indexes created ===
SELECT
    OBJECT_NAME(i.object_id) AS table_name,
    i.name AS index_name,
    i.type_desc,
    COUNT(*) AS column_count
FROM sys.indexes i
JOIN sys.index_columns ic ON i.object_id = ic.object_id AND i.index_id = ic.index_id
WHERE OBJECT_NAME(i.object_id) IN (
    'service_churn_truckroll_base',
    'service_churn_res_latest',
    'service_churn_com_latest',
    'service_churn_call_monthly_agg',
    'service_churn_account_mac_map',
    'service_churn_modem_health_latest'
)
GROUP BY i.object_id, i.index_id, i.name, i.type_desc
ORDER BY table_name, index_name;
