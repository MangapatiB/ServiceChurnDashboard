# Churn Service Dashboard QA Web Test Scenarios

## Purpose

This document lists the maximum practical webpage test scenarios for the Churn Service Dashboard so the QA team can validate functionality, UI, UX, latency, usability, live data behavior, and error handling.

## Scope

This checklist covers manual browser testing for:

- Main dashboard page
- Operations view page
- Filter behavior
- Data rendering
- Pagination and sorting
- Auto-refresh behavior
- Error and fallback behavior
- Performance and latency
- Responsive behavior
- Accessibility basics

## Test Environments

QA should run these scenarios in the following modes when available:

1. Mock mode
2. Live Databricks mode
3. Live Databricks mode with SQL Server modem health enabled
4. Degraded mode where one or more external services are intentionally unavailable

## Recommended Browsers

- Google Chrome
- Microsoft Edge
- Mozilla Firefox

## Recommended Screen Sizes

- Desktop: 1920x1080
- Laptop: 1366x768
- Tablet: 768x1024
- Mobile width check: 390x844 or similar

## Entry Point Scenarios

### 1. Application Launch

- Verify the root page loads successfully from `/`
- Verify the page title shows `Churn Service Dashboard`
- Verify the page does not show blank sections on initial load
- Verify the Sparklight banner image loads correctly
- Verify the first dashboard render completes without browser console errors

### 2. Operations View Launch

- Verify `Operations view` link opens `/operations`
- Verify the operations page loads without errors
- Verify the operations page keeps the current location, limit, and segment in the URL when navigated from the main page
- Verify the `Main dashboard` link from operations returns to the dashboard with the same filters retained

## Filter Scenarios

### 3. Customer Type Filter

- Verify default customer type is correct on initial load
- Verify changing from `Residential` to `Business` refreshes the dashboard data
- Verify changing from `Business` back to `Residential` refreshes the dashboard data
- Verify selected customer type remains visible after refresh
- Verify selected customer type remains in the URL after refresh

### 4. Billing City Filter

- Verify `All locations` loads data successfully
- Verify selecting a valid location refreshes the dashboard
- Verify switching between two valid locations refreshes correctly each time
- Verify clearing the location back to `All locations` refreshes correctly
- Verify selected location remains visible after refresh
- Verify selected location remains in the URL after refresh

### 5. Limit Filter

- Verify a small limit such as `12` loads successfully
- Verify a medium limit such as `25` loads successfully
- Verify a larger limit such as `100` loads successfully if supported
- Verify entering `1` loads one-account level data without layout errors
- Verify entering `0` is handled safely
- Verify entering a negative value is handled safely
- Verify entering a blank value is handled safely
- Verify entering a non-numeric value is handled safely
- Verify entering a very large value such as `100000` does not crash the page
- Verify the limit shown in the dashboard matches the requested limit when accepted

### 6. Filter Combination Scenarios

- Verify customer type plus location plus limit together produce valid results
- Verify rapidly changing filters and clicking `Apply filters` still leaves the page in a valid state
- Verify repeated filter changes do not duplicate rows or break pagination

## Main Dashboard Content Scenarios

### 7. Summary Strip

- Verify the summary strip renders cards on page load
- Verify summary content changes when filters change
- Verify empty or fallback data does not break the summary layout

### 8. KPI Section

- Verify KPI cards render on load
- Verify KPI cards update after filter changes
- Verify KPI values are readable and do not overflow the cards
- Verify the empty-state message is shown when KPI data is unavailable

### 9. Highest Risk Accounts Table

- Verify the customer table renders rows on valid data
- Verify each row shows customer ID, geo, phone, churn probability, drivers, last event, and next action
- Verify long text inside drivers or actions does not break the table layout
- Verify no-data conditions show the empty-state message
- Verify high-risk styling appears correctly for different churn values

### 10. Customer Sorting

- Verify default sort order is applied on load
- Verify switching sort from `Desc` to `Asc` changes displayed order
- Verify switching sort back from `Asc` to `Desc` restores descending order
- Verify sorting does not reset filters incorrectly
- Verify sorting on empty data does not cause errors

### 11. Customer Pagination

- Verify `Previous 15` is disabled on the first page
- Verify `Next 15` is enabled when more than 15 rows exist
- Verify clicking `Next 15` moves to the next set of customers
- Verify clicking `Previous 15` returns to the previous set of customers
- Verify pagination summary text updates correctly for each page
- Verify pagination resets to page 1 after filters change
- Verify pagination works correctly when total row count is less than 15
- Verify pagination works correctly when total row count is exactly 15
- Verify pagination works correctly when total row count is greater than 15

### 12. Signal Mix Section

- Verify signal mix rows render on load
- Verify percentages are displayed correctly
- Verify visual bars render without layout issues
- Verify the signal section updates after filters change
- Verify empty-state text shows when no signal data exists

### 13. Call Data Section

- Verify call data KPI cards render when data exists
- Verify call data detail cards render when data exists
- Verify the scope label renders meaningful text
- Verify empty-state text appears when no call data exists
- Verify call data changes after filters change
- Verify large watchlist sizes do not visually break the call-data section

### 14. Modem Health Section

- Verify modem health KPI cards render when modem data exists
- Verify modem telemetry table renders rows when data exists
- Verify modem status badges display correct styling for `Online`, `Offline`, and `Unavailable`
- Verify missing modem fields display `-` or blank safely without layout issues
- Verify empty-state text appears when modem data is not available
- Verify horizontal scrolling works for the modem table on smaller screens
- Verify large modem row counts do not break page rendering

## Operations View Scenarios

### 15. Operations Page Data

- Verify the operations page renders the market watchlist table
- Verify each row shows geo, flagged accounts, average risk, 90+ risk count, contactable count, top driver, tier, and recommended action
- Verify the empty-state message appears when no market rows exist
- Verify operations page filters work correctly

### 16. Operations Page Sorting

- Verify default tier sorting works on initial load
- Verify changing tier sort from `Asc` to `Desc` reorders rows correctly
- Verify sorting does not remove or duplicate rows

## Refresh Scenarios

### 17. Manual Refresh Behavior

- Verify clicking `Apply filters` disables the button while refresh is in progress
- Verify button text changes to `Updating...` during refresh
- Verify button text returns after refresh completes
- Verify one completed refresh updates all visible sections consistently

### 18. Auto Refresh Behavior

- Verify `Disabled` stops periodic refresh
- Verify configured refresh interval starts periodic refresh
- Verify auto-refresh keeps the current filters
- Verify auto-refresh does not break sorting or pagination unexpectedly
- Verify long-running refreshes do not leave the button permanently disabled
- Verify repeated auto-refresh does not create visible UI corruption

## Error Handling Scenarios

### 19. Empty Data Scenarios

- Verify empty data for KPI section shows a user-friendly message
- Verify empty customer data shows a user-friendly message
- Verify empty signal mix shows a user-friendly message
- Verify empty call data shows a user-friendly message
- Verify empty modem data shows a user-friendly message
- Verify empty operations data shows a user-friendly message

### 20. Fallback and Degraded Data Scenarios

- Verify the page still loads when Databricks is unavailable
- Verify the page still loads when modem SQL enrichment is unavailable
- Verify fallback data does not break the UI layout
- Verify degraded mode messaging is understandable to the user
- Verify the user can still change filters during degraded mode

### 21. Invalid URL Scenarios

- Verify invalid `segment` values are handled safely
- Verify invalid `limit` values are handled safely
- Verify unknown `location` values are handled safely
- Verify manually editing the query string does not break the page

## Performance and Latency Scenarios

### 22. Basic Response-Time Checks

- Measure initial page-load time at `limit=12`
- Measure dashboard API response time at `limit=12`
- Measure dashboard API response time at `limit=25`
- Measure dashboard API response time at `limit=100`
- Compare load behavior between mock mode and live mode

### 23. High-Load UI Checks

- Verify the UI remains usable during slow API responses
- Verify the page does not appear permanently frozen after a slow request
- Verify repeated refreshes during heavy load do not duplicate content
- Verify browser memory and CPU usage remain reasonable during repeated refreshes

## Browser and Session Scenarios

### 24. Reload and Navigation Checks

- Verify browser refresh keeps the page in a valid state
- Verify browser back and forward navigation behave correctly with filter changes
- Verify bookmarked URLs with query parameters open correctly
- Verify opening the dashboard in a new tab preserves valid rendering

### 25. Multi-Tab Checks

- Verify two dashboard tabs can stay open without breaking each other
- Verify changing filters in one tab does not corrupt data rendering in another tab

## Responsive and Layout Scenarios

### 26. Desktop Layout

- Verify sections align correctly on wide screens
- Verify cards, tables, and filters are visually balanced
- Verify no overlapping text or controls appear

### 27. Tablet Layout

- Verify sections stack correctly on tablet width
- Verify filters remain usable on tablet width
- Verify tables are still readable with horizontal scrolling

### 28. Mobile Width Layout

- Verify the top bar remains readable on small screens
- Verify filters remain clickable and do not overlap
- Verify customer table can still be reviewed with horizontal scrolling
- Verify modem health table remains accessible with horizontal scrolling
- Verify no buttons are pushed off-screen

## Accessibility Scenarios

### 29. Keyboard Navigation

- Verify all filters can be reached using the keyboard only
- Verify dropdowns can be changed using the keyboard only
- Verify `Apply filters` can be activated using the keyboard only
- Verify pagination buttons can be used using the keyboard only
- Verify focus order is logical across the page

### 30. Basic Accessibility Checks

- Verify form controls have visible labels
- Verify interactive elements have visible focus states
- Verify text remains readable against background colors
- Verify status badges are understandable without relying only on color
- Verify the banner image has alternative text

## Visual and Content Quality Scenarios

### 31. Text and Label Validation

- Verify all headings are spelled correctly
- Verify labels match the actual data shown
- Verify no placeholder or broken text appears in production-like views
- Verify static explanatory text does not conflict with current data values

### 32. Data Formatting Checks

- Verify churn probability displays with the expected format
- Verify percentages appear consistently across cards and sections
- Verify timestamps display consistently
- Verify blank values display safely without showing `undefined` or `null`

## Console and Network Checks

### 33. Browser Console Checks

- Verify the dashboard loads without JavaScript errors
- Verify filter changes do not create console errors
- Verify auto-refresh does not create repeated console errors

### 34. Network Checks

- Verify `/api/dashboard` returns successful responses during normal use
- Verify failed API calls are visible in browser network tools during degraded testing
- Verify large requests do not trigger malformed responses

## Exit Criteria For QA Sign-Off

QA can recommend sign-off only after:

- All critical page-load scenarios pass
- All filter scenarios pass
- Main dashboard sections render correctly
- Operations view renders correctly
- No major console errors remain
- No blocking layout issues remain on desktop or tablet
- Slow responses do not permanently break the UI
- Degraded or fallback behavior is understandable to the user

## Priority Execution Order For QA Team

If time is limited, run scenarios in this order:

1. Application launch and main dashboard rendering
2. Filter behavior and URL state retention
3. Customer sorting and pagination
4. Call-data and modem-health rendering
5. Operations view behavior
6. Auto-refresh and slow-response behavior
7. Empty, degraded, and fallback scenarios
8. Responsive and accessibility checks

## Summary

This document is intended to give the QA team a broad webpage scenario set that covers the main failure points and usability risks in the current dashboard. The highest-risk areas are filter behavior, slow live refreshes, pagination accuracy, degraded-data handling, and dense table rendering on smaller screens.