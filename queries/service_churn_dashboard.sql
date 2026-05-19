select *
from values
  ('kpi', 'High-risk customers', '1284', '+8.4%', 'risk', null, null, null, null, null),
  ('kpi', 'Repeat trouble calls', '3912', '+4.1%', 'warning', null, null, null, null, null),
  ('kpi', 'Truck rolls / 30d', '864', '-2.7%', 'good', null, null, null, null, null),
  ('kpi', 'Service health score', '72', '-5 pts', 'warning', null, null, null, null, null),
  ('kpi', 'Competitive risk markets', '11', '+2', 'risk', null, null, null, null, null),
  ('geo', 'Boise North', 'BN-114', 'Tier 1', '58', '21', '2', '184', '7', 'Plant sweep + proactive outreach'),
  ('geo', 'Phoenix West', 'PW-208', 'Tier 1', '61', '24', '3', '162', '5', 'Appointment backlog reset'),
  ('geo', 'Twin Falls', 'TF-031', 'Tier 2', '69', '34', '7', '113', '3', 'Watch list + SMS update'),
  ('geo', 'Missoula East', 'ME-019', 'Tier 3', '81', '47', '14', '74', '1', 'Monitor'),
  ('customer', 'A-104923', '96', '3 calls, modem distress, outage cluster', '2h ago', 'Outbound call + expedite truck roll', 'Boise North', null, null, null),
  ('customer', 'A-204166', '93', '2 truck rolls, low NPS area', '5h ago', 'Supervisor review', 'Phoenix West', null, null, null),
  ('customer', 'A-992451', '88', '2 outages, appointment slip', '7h ago', 'SMS apology + credit check', 'Twin Falls', null, null, null),
  ('customer', 'A-552061', '86', '4 calls in 90d, poor node health', '11h ago', 'Priority queue routing', 'Boise North', null, null, null)
as dashboard_records(
  record_type,
  field_1,
  field_2,
  field_3,
  field_4,
  field_5,
  field_6,
  field_7,
  field_8,
  field_9
)
