/*
Performance indexes for ServiceChurnDashboard call-data and watchlist queries.
Apply in SQL Server as a user with CREATE INDEX permission.
Run during a low-traffic window because large indexes can take time.
*/

USE [ServiceChurnDashboard];
GO

/*
1) Truckroll watchlist query pattern:
   WHERE SubscriberAccountNumber IS NOT NULL
     AND UPPER(BillingCity) = ? (optional)
   ORDER BY UPPER(BillingCity), SubscriberAccountNumber
*/
IF NOT EXISTS (
    SELECT 1
    FROM sys.indexes
    WHERE name = 'IX_truckroll_city_subscriber'
      AND object_id = OBJECT_ID('dbo.service_churn_truckroll_base')
)
BEGIN
    CREATE NONCLUSTERED INDEX IX_truckroll_city_subscriber
    ON dbo.service_churn_truckroll_base (BillingCity, SubscriberAccountNumber)
    INCLUDE (LegacyAccountNumber, PhoneNumber);
END
GO

/*
2) Call records query pattern (current hot path):
   WHERE CustomerType = ?
     AND SubscriberAccount IN (...) OR CustomerAccount IN (...)
   ORDER BY MonthStart DESC, NumberOfCalls DESC

Create two selective indexes so SQL Server can seek by either account key.
*/
IF NOT EXISTS (
    SELECT 1
    FROM sys.indexes
    WHERE name = 'IX_call_records_subscriber'
      AND object_id = OBJECT_ID('dbo.service_churn_call_records_monthly')
)
BEGIN
    CREATE NONCLUSTERED INDEX IX_call_records_subscriber
    ON dbo.service_churn_call_records_monthly (
        CustomerType,
        SubscriberAccount,
        MonthStart DESC,
        NumberOfCalls DESC
    )
    INCLUDE (
        CustomerAccount,
        TotalDurationMinutes,
        AvgDurationMinutes,
        ClientSentiment,
        IsResolved
    );
END
GO

IF NOT EXISTS (
    SELECT 1
    FROM sys.indexes
    WHERE name = 'IX_call_records_customer'
      AND object_id = OBJECT_ID('dbo.service_churn_call_records_monthly')
)
BEGIN
    CREATE NONCLUSTERED INDEX IX_call_records_customer
    ON dbo.service_churn_call_records_monthly (
        CustomerType,
        CustomerAccount,
        MonthStart DESC,
        NumberOfCalls DESC
    )
    INCLUDE (
        SubscriberAccount,
        TotalDurationMinutes,
        AvgDurationMinutes,
        ClientSentiment,
        IsResolved
    );
END
GO

/*
3) Call monthly aggregation query pattern:
   WHERE CustomerType = ? AND AccountNumber IN (...)
   ORDER BY MonthStart
*/
IF NOT EXISTS (
    SELECT 1
    FROM sys.indexes
    WHERE name = 'IX_call_monthly_customer_account_month'
      AND object_id = OBJECT_ID('dbo.service_churn_call_monthly_agg')
)
BEGIN
    CREATE NONCLUSTERED INDEX IX_call_monthly_customer_account_month
    ON dbo.service_churn_call_monthly_agg (CustomerType, AccountNumber, MonthStart)
    INCLUDE (
        NumberOfCalls,
        ContactMonthStart,
        AverageAgentTalkMin,
        AverageTotalContactDurationMin,
        TotalAgentTalkMin,
        TotalContactDurationMin
    );
END
GO

/*
4) Churn lookup query pattern:
   WHERE SubscriberAccountNumber IN (...)
   ORDER BY ChurnProbability DESC
*/
IF NOT EXISTS (
    SELECT 1
    FROM sys.indexes
    WHERE name = 'IX_res_churn_subscriber'
      AND object_id = OBJECT_ID('dbo.service_churn_res_latest')
)
BEGIN
    CREATE NONCLUSTERED INDEX IX_res_churn_subscriber
    ON dbo.service_churn_res_latest (SubscriberAccountNumber)
    INCLUDE (ChurnProbability, PredictionMonth, Top1Feature, Top2Feature, Top3Feature);
END
GO

IF NOT EXISTS (
    SELECT 1
    FROM sys.indexes
    WHERE name = 'IX_com_churn_subscriber'
      AND object_id = OBJECT_ID('dbo.service_churn_com_latest')
)
BEGIN
    CREATE NONCLUSTERED INDEX IX_com_churn_subscriber
    ON dbo.service_churn_com_latest (SubscriberAccountNumber)
    INCLUDE (ChurnProbability, PredictionMonth, Top1Feature, Top2Feature, Top3Feature);
END
GO

/*
5) Optional: refresh optimizer stats after index creation.
*/
UPDATE STATISTICS dbo.service_churn_call_records_monthly WITH FULLSCAN;
UPDATE STATISTICS dbo.service_churn_call_monthly_agg WITH FULLSCAN;
UPDATE STATISTICS dbo.service_churn_truckroll_base WITH FULLSCAN;
UPDATE STATISTICS dbo.service_churn_res_latest WITH FULLSCAN;
UPDATE STATISTICS dbo.service_churn_com_latest WITH FULLSCAN;
GO
