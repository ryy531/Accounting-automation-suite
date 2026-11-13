# ExampleCo NocoDB Platform Reference

> Live metadata verified: 2026-06-18T09:35:00-06:00
> Audience: internal ExampleCo projects and automation agents.
> This document contains no credentials, customer records, contact details, or webhook destinations.

## 1. Purpose And Authority

This is the durable handoff document for the shared ExampleCo NocoDB platform. Use it before inspecting live metadata or scanning the shared drive. It describes the current four-table model, integration contract, operational constraints, and known limitations.

Authority boundaries:

- Live table metadata and row counts in this document were read from NocoDB on the verification date above.
- Deployment and physical SQLite facts were verified separately on 2026-06-11 and are not re-probed here.
- Row counts, views, filters, and hooks can change. Re-read only the specific live resource needed by the task.
- Project knowledge-base documents `01_big_picture.md` and `02_roadmap.md` remain authoritative for strategy and roadmap.

## 2. Deployment And Identifiers

| Item | Current value |
| --- | --- |
| NocoDB version | 2026.04.5 |
| Hosting | Self-hosted on the company LAN |
| Runtime | Docker container `nocodb` |
| Datastore | SQLite, `/usr/app/data/noco.db` inside the container |
| Workspace ID | `w5ebi83p` |
| Base | `ExampleCo` / `pfzu36271b5cg7n` |
| Source ID | `bcnzeycyux0hnat` |
| Credential source | `_lib/config/.env` through `_lib/config/settings.py` |
| Data access contract | `_lib/data_access.py` |

Do not copy the NocoDB URL or token into project code or documentation. Business code must use `_lib/data_access.py`; it must not call NocoDB REST directly.

## 3. Core Tables

| Table | Table ID | Live rows | Fields | Views | Hooks |
| --- | --- | ---: | ---: | ---: | ---: |
| Companies | `muoolwcrncbs21t` | 166 | 65 | 8 | 1 |
| People | `m6eek92z4tf1d17` | 112 | 17 | 1 | 0 |
| Engagements | `mmm0q9s5tmk044s` | 473 | 31 | 5 | 2 |
| Events | `mntpl2d8ji03ac2` | 118 | 12 | 2 | 0 |

Model intent:

- **Companies** stores organization-level master data and recurring-service configuration.
- **People** stores natural-person contact records.
- **Engagements** stores work items such as Payroll, GST, PST, T2, Bookkeeping, Annual Return, and T1.
- **Events** is the append-only activity/history stream for automation and user actions.

Current relationship limitation: `Companies.People`, `Companies.Engagements`, `People.Company`, `People.Engagements`, `Engagements.Company`, `Engagements.Person`, `Engagements.Events`, and `Events.Engagement` are text-like fields, not enforced NocoDB relation fields. Other projects must not assume referential integrity.

## 4.1. Companies

Table ID: `muoolwcrncbs21t`  
Live row count: **166**

### Fields

| Order | Display field | Field ID | Physical column | NocoDB type | DB type | PK | Required | Default | Options |
| ---: | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 1 | Id | `cb92ome4w41gasf` | `id` | ID | integer | yes | yes |  |  |
| 2 | CreatedAt | `czf6c8zc2w3l93k` | `created_at` | CreatedTime | datetime | no | no |  |  |
| 3 | UpdatedAt | `c852pn4vqurhs34` | `updated_at` | LastModifiedTime | datetime | no | no |  |  |
| 4 | nc_created_by | `cwxxdxvt1v3gff3` | `created_by` | CreatedBy | varchar | no | no |  |  |
| 5 | nc_updated_by | `c5g0n1fhvhna8j0` | `updated_by` | LastModifiedBy | varchar | no | no |  |  |
| 6 | nc_order | `cr05h6rv3nphi3y` | `nc_order` | Order | real | no | no |  |  |
| 7 | __nc_deleted | `chsmkl2spxllqcs` | `__nc_deleted` | Deleted | boolean | no | no |  |  |
| 8 | Legal Name | `ctbgzznmwpp6ioo` | `Legal_Name` | SingleLineText | varchar | no | no |  |  |
| 9 | Entity Number | `c6b5bylqsdsilxp` | `Entity_Number` | SingleLineText | varchar | no | no |  |  |
| 10 | Business Number | `ca54z2m5wj733gh` | `Business_Number` | SingleLineText | varchar | no | no |  |  |
| 11 | Operating Name | `chkg39a3ftftbvd` | `Operating_Name` | SingleLineText | varchar | no | no |  |  |
| 12 | Incorporate Date | `cn3t7g8e0wk2t3x` | `Incorporate_Date` | Date | date | no | no |  |  |
| 13 | Incorporation Jurisdiction | `cfg53ejezfgvqym` | `Incorporation_Jurisdiction` | SingleLineText | varchar | no | no |  |  |
| 14 | Language | `cxz64n9sk78obez` | `Language` | SingleSelect | text | no | no |  | Chinese; English; Vietnamese |
| 15 | Client Type | `citnsuqftvuod2y` | `Client_Type` | SingleSelect | text | no | no |  | Annual; Monthly |
| 16 | Contract Services | `cjb0wrn1zbkp68d` | `Contract_Services` | LongText | text | no | no |  |  |
| 17 | Monthly Fee | `c46d5am6y4yai60` | `Monthly_Fee` | Number | integer | no | no |  |  |
| 18 | B/B+ Charge | `cmaevu4u4awdnng` | `B_B__Charge` | Number | integer | no | no |  |  |
| 19 | T2/Self-employed Charge | `c5g00y0f4gzdf4p` | `T2_Self_employed_Charge` | Number | integer | no | no |  |  |
| 20 | Payment Method | `cdi658mn6409ao0` | `Payment_Method` | SingleSelect | text | no | no |  | Cheque; PAD; E-Transfer |
| 21 | T2 YE | `cqs8fwvvxgj7j5w` | `T2_YE` | SingleLineText | varchar | no | no |  |  |
| 22 | GST Frequency | `cpjp7p3rblnp84s` | `GST_Frequency` | SingleSelect | text | no | no |  | Quarterly; Annual; Monthly |
| 23 | PST No. | `cscoel20m4bvjw4` | `PST_No_` | SingleLineText | varchar | no | no |  |  |
| 24 | PST Frequency | `cjf8bby6wnifitk` | `PST_Frequency` | SingleSelect | text | no | no |  | Annual; Quarterly; Monthly |
| 25 | Owner Name | `czrvoiq35a8kq7b` | `Owner_Name` | SingleLineText | varchar | no | no |  |  |
| 26 | Owner Email | `ccei7417h614z6r` | `Owner_Email` | Email | varchar | no | no |  |  |
| 27 | 2nd Owner Name | `cky9k3otn2uy62u` | `2nd_Owner_Name` | SingleLineText | varchar | no | no |  |  |
| 28 | Owner Phone | `ci4h0u03n65h6qx` | `Owner_Phone` | SingleLineText | varchar | no | no |  |  |
| 29 | People | `cexsn0ershmwqd5` | `People` | SingleLineText | varchar | no | no |  |  |
| 30 | Engagements | `cyivgizdc21tr0o` | `Engagements` | SingleLineText | varchar | no | no |  |  |
| 31 | Is Payroll Client | `czkhrzkk4h6apo5` | `Is_Payroll_Client` | Checkbox | boolean | no | no | 0 |  |
| 32 | Pay Frequency | `c825qfn89iieo5u` | `Pay_Frequency` | SingleSelect | text | no | no |  | Monthly; Yearly; Semi-monthly; Bi-weekly |
| 33 | Pay Period End Rule | `c5aolwnsxktpecd` | `Pay_Period_End_Rule` | SingleLineText | varchar | no | no |  |  |
| 34 | Payroll History Folder | `cvivky4qntj0r7f` | `Payroll_History_Folder` | SingleLineText | varchar | no | no |  |  |
| 35 | QB File Folder | `c3rv69exnwbndt7` | `QB_File_Folder` | SingleLineText | varchar | no | no |  |  |
| 36 | Payroll Notes | `ct7adpk050t54q0` | `Payroll_Notes` | LongText | text | no | no |  |  |
| 37 | Payroll Schedule 2 Active | `cj9o7wzoju0ot8g` | `Payroll_Schedule_2_Active` | Checkbox | boolean | no | no | 0 |  |
| 38 | Payroll Schedule 2 Frequency | `cvtftefum7z4040` | `Payroll_Schedule_2_Frequency` | SingleSelect | text | no | no |  | Monthly; Yearly; Semi-monthly; Bi-weekly |
| 39 | Payroll Schedule 2 Period End Rule | `c443g8w2eb5i51d` | `Payroll_Schedule_2_Period_End_Rule` | SingleLineText | varchar | no | no |  |  |
| 40 | GST Filed Until | `ctm66tinkfsxzun` | `GST_Filed_Until` | Date | date | no | no |  |  |
| 41 | GST Due Next Period | `cerqmfgncfmh9q0` | `GST_Due_Next_Period` | Date | date | no | no |  |  |
| 42 | PST Filed Until | `c7gtxqus5yworp8` | `PST_Filed_Until` | Date | date | no | no |  |  |
| 43 | PST Due Next Period | `c5t44am1xbp3e9a` | `PST_Due_Next_Period` | Date | date | no | no |  |  |
| 44 | T2 Filed Until | `ccmjnk3n57lcjgm` | `T2_Filed_Until` | Date | date | no | no |  |  |
| 45 | T2 Due Next Period | `cizqijdb2wr6lxr` | `T2_Due_Next_Period` | Date | date | no | no |  |  |
| 46 | Bookkeeping Filed Until | `chek3u6hfkcgk28` | `Bookkeeping_Filed_Until` | Date | date | no | no |  |  |
| 47 | Annual Return Month | `cn1k5fcafzbt53p` | `Annual_Return_Month` | SingleSelect | text | no | no |  | Jan; Feb; Mar; Apr; May; Jun; Jul; Aug; Sep; Oct; Nov; Dec |
| 48 | GST Due Next Period Calculated | `ci4egzjbxiceqna` | `GST_Due_Next_Period_Calculated` | Date | date | no | no |  |  |
| 49 | PST Due Next Period Calculated | `czl87hxueibxjmj` | `PST_Due_Next_Period_Calculated` | Date | date | no | no |  |  |
| 50 | T2 Due Next Period Calculated | `c3m7gpevh351job` | `T2_Due_Next_Period_Calculated` | Date | date | no | no |  |  |
| 51 | Bookkeeping Due Date Calculated | `c3cahpv1lh36voh` | `Bookkeeping_Due_Date_Calculated` | Date | date | no | no |  |  |
| 52 | Workbench Next Action | `c26sn9vrtln2pnr` | `Workbench_Next_Action` | LongText | text | no | no |  |  |
| 53 | Workbench Next Action Date | `cap1cqpxfyu0v8v` | `Workbench_Next_Action_Date` | Date | date | no | no |  |  |
| 54 | Workbench Needs Review | `cmho93vsysy689s` | `Workbench_Needs_Review` | Checkbox | boolean | no | no | 0 |  |
| 55 | Workbench Review Reason | `cs2djoqi3x3isll` | `Workbench_Review_Reason` | LongText | text | no | no |  |  |
| 56 | Workbench Last Updated At | `cavi5375jc8z91f` | `Workbench_Last_Updated_At` | DateTime | datetime | no | no |  |  |
| 57 | Workbench Source | `c0aah9wfu8my69b` | `Workbench_Source` | SingleSelect | text | no | no |  |  |
| 58 | Workbench Priority | `ccv5a7o90yap95q` | `Workbench_Priority` | SingleSelect | text | no | no |  | P0 Needs Review; P1 Overdue; P2 Due This Week; P3 Due Soon; P4 OK; Blocked |
| 59 | GST Next Period End | `c1xb4bvgyjda2yk` | `GST_Next_Period_End` | Date | date | no | no |  |  |
| 60 | GST Urgency | `c2a9h9meg52kuqb` | `GST_Urgency` | SingleSelect | text | no | no |  | Needs Review; Overdue; Due This Week; Due Soon; OK; Blocked |
| 61 | PST Next Period End | `c87e3mwm03xfa8j` | `PST_Next_Period_End` | Date | date | no | no |  |  |
| 62 | PST Urgency | `c2tymanso1b0nbz` | `PST_Urgency` | SingleSelect | text | no | no |  | Needs Review; Overdue; Due This Week; Due Soon; OK; Blocked |
| 63 | T2 Urgency | `cz901nky36y2vt4` | `T2_Urgency` | SingleSelect | text | no | no |  | Needs Review; Overdue; Due This Week; Due Soon; OK; Blocked |
| 64 | Bookkeeping Urgency | `cy1878dc4ofxfjk` | `Bookkeeping_Urgency` | SingleSelect | text | no | no |  | Needs Review; Overdue; Due This Week; Due Soon; OK; Blocked |
| 65 | client status | `cbvqnfgftgmsuyl` | `client_status` | SingleSelect | text | no | no |  | active; inactive |

### Views

| View | View ID | Type | Filters | Sorts |
| --- | --- | --- | --- | --- |
| Master Client List | `vws5urcxwz9oqvbx` | 3 |  |  |
| Client type | `vwyrqk63snjg6qfk` | 4 |  |  |
| Payroll Frequency | `vwgt6o2zdajthlci` | 4 |  |  |
| Client Workbench - Status | `vwtkpfpwkwpxdvho` | 3 |  | Legal Name asc |
| Due Dates Comparison | `vw8bzuv66cm64osc` | 3 |  | Legal Name asc |
| Needs Review | `vw6sxadwtusqx405` | 3 | and Workbench Needs Review checked | Legal Name asc |
| For GST Update | `vwnf358ay872aiqt` | 3 |  |  |
| Workbench - Due Mismatch Review | `vw0uyxegkuobx65b` | 3 | and Workbench Priority eq P0 Needs Review; and Workbench Next Action like due mismatch |  |

### Webhooks

Webhook destinations and headers are intentionally omitted.

| Hook | Hook ID | Active | Event | Operation |
| --- | --- | --- | --- | --- |
| companies filed until auto dates | `hkvpdiz7to5jijcn` | 1 | after | ['update'] |

## 4.2. People

Table ID: `m6eek92z4tf1d17`  
Live row count: **112**

### Fields

| Order | Display field | Field ID | Physical column | NocoDB type | DB type | PK | Required | Default | Options |
| ---: | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 1 | Id | `cdh6iz3axe59ir0` | `id` | ID | integer | yes | yes |  |  |
| 2 | CreatedAt | `cl80kqj1f0q8w26` | `created_at` | CreatedTime | datetime | no | no |  |  |
| 3 | UpdatedAt | `cd259hok20c6amt` | `updated_at` | LastModifiedTime | datetime | no | no |  |  |
| 4 | nc_created_by | `cb3qo6ix6m1cejl` | `created_by` | CreatedBy | varchar | no | no |  |  |
| 5 | nc_updated_by | `ct1oayta54uxbtn` | `updated_by` | LastModifiedBy | varchar | no | no |  |  |
| 6 | nc_order | `cizscskwe52lhjg` | `nc_order` | Order | real | no | no |  |  |
| 7 | __nc_deleted | `cy34g2l8h52ir2i` | `__nc_deleted` | Deleted | boolean | no | no |  |  |
| 8 | Full Name | `c4wmzq6ircwqp1v` | `Full_Name` | SingleLineText | varchar | no | no |  |  |
| 9 | Email | `cdv4uf7ee6i83e7` | `Email` | Email | varchar | no | no |  |  |
| 10 | Phone | `cpuqx1hienj0tmz` | `Phone` | PhoneNumber | varchar | no | no |  |  |
| 11 | Language | `c5whiaenj7g8bc7` | `Language` | SingleSelect | text | no | no |  | Chinese; English; Vietnamese; 1 |
| 12 | Company | `cbzt5b94oxbt6ko` | `Company` | SingleLineText | varchar | no | no |  |  |
| 13 | Is Primary Owner | `c0k4xs0dea3n25b` | `Is_Primary_Owner` | Checkbox | boolean | no | no |  |  |
| 14 | Notes | `c3pms1i79jb0zkm` | `Notes` | LongText | text | no | no |  |  |
| 15 | Engagements | `cowquuk2cltq1jw` | `Engagements` | SingleLineText | varchar | no | no |  |  |
| 16 | Related Companies | `cylcra0jw42ju9d` | `Related_Companies` | LongText | text | no | no |  |  |
| 17 | client status | `c699sy8la96zy6d` | `client_status` | SingleSelect | text | no | no |  | active; inactive |

### Views

| View | View ID | Type | Filters | Sorts |
| --- | --- | --- | --- | --- |
| People | `vwr956vfujjvk1b2` | 3 |  |  |

### Webhooks

No hooks were returned (`/api/v2/meta/tables/m6eek92z4tf1d17/hooks`).

## 4.3. Engagements

Table ID: `mmm0q9s5tmk044s`  
Live row count: **473**

### Fields

| Order | Display field | Field ID | Physical column | NocoDB type | DB type | PK | Required | Default | Options |
| ---: | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 1 | Id | `ch7w7xtx9xyzn7g` | `id` | ID | integer | yes | yes |  |  |
| 2 | CreatedAt | `cg67zaaznbido87` | `created_at` | CreatedTime | datetime | no | no |  |  |
| 3 | UpdatedAt | `c3k8jzucrltrolg` | `updated_at` | LastModifiedTime | datetime | no | no |  |  |
| 4 | nc_created_by | `c8l17f36eufz33q` | `created_by` | CreatedBy | varchar | no | no |  |  |
| 5 | nc_updated_by | `c0evoypjmhhlwo4` | `updated_by` | LastModifiedBy | varchar | no | no |  |  |
| 6 | nc_order | `czmorelwpl226o8` | `nc_order` | Order | real | no | no |  |  |
| 7 | __nc_deleted | `cqd2tabbb3vrohp` | `__nc_deleted` | Deleted | boolean | no | no |  |  |
| 8 | Name | `cesn2zifkpkeghe` | `Name` | SingleLineText | varchar | no | no |  |  |
| 9 | Type | `c00q3ojidglryfm` | `Type` | SingleSelect | text | no | no |  | T1; T2; GST; PST; Bookkeeping; Annual Return; Payroll |
| 10 | Status | `c3ph6zhg2o58n1n` | `Status` | SingleSelect | text | no | no |  | Payroll \| Waiting Info; Payroll \| Processing; Payroll \| Completed; Payroll \| Archived; Not Started; Complete |
| 11 | Company | `chlsc5wa0m8pthn` | `Company` | SingleLineText | varchar | no | no |  |  |
| 12 | Person | `cmckad0xr6atbew` | `Person` | SingleLineText | varchar | no | no |  |  |
| 13 | Year | `c8nwwjqtf2rjjvx` | `Year` | Number | integer | no | no |  |  |
| 14 | Due Date | `c13vf71flp1dt8q` | `Due_Date` | Date | date | no | no |  |  |
| 15 | Notes | `c69yl3vxhlyloyv` | `Notes` | LongText | text | no | no |  |  |
| 16 | Events | `cxs6wogtdhur3tq` | `Events` | SingleLineText | varchar | no | no |  |  |
| 17 | Payroll Period Start | `cpuc4hiho2orv2h` | `Pay_Roll_Period_Start` | Date | date | no | no |  |  |
| 18 | Payroll Period End | `cto9qnezurxaij2` | `Pay_Roll_Period_End` | Date | date | no | no |  |  |
| 19 | Payroll Has Question | `cg0lu7navj7cn79` | `Pay_Roll_Has_Question` | Checkbox | boolean | no | no | 0 |  |
| 20 | Assigned To | `cxsnwx1r1e2wp13` | `Assigned_To` | SingleSelect | text | no | no |  | Riley; Morgan; Sherry; Alex |
| 22 | Frequency | `cm1isyz675usfjg` | `Frequency` | SingleSelect | text | no | no |  | Monthly; Quarterly; Annual |
| 23 | Period Start | `ca3d59xyx6apzds` | `Period_Start` | Date | date | no | no |  |  |
| 24 | Period End | `ca6zeto5k8mxfkk` | `Period_End` | Date | date | no | no |  |  |
| 25 | Priority | `c75zml8i7oykg7j` | `Priority` | SingleSelect | text | no | no |  | P0 Needs Review; P1 Overdue; P2 Due This Week; P3 Due Soon; P4 OK; Blocked |
| 26 | Next Action | `ci97ov0it6hc70k` | `Next_Action` | LongText | text | no | no |  |  |
| 27 | Source | `c7rzuwvjk0cfire` | `Source` | SingleSelect | text | no | no |  | Auto; Manual; Excel Bootstrap |
| 28 | Needs Review | `cvztiwjtt8gqndj` | `Needs_Review` | Checkbox | boolean | no | no | 0 |  |
| 29 | Review Reason | `cgfy2f3okcq3lw6` | `Review_Reason` | LongText | text | no | no |  |  |
| 30 | T2 Status | `cbm6tnztjnnjd12` | `t2_status` | MultiSelect | text | no | no | stage \| not started | stage \| not started; stage \| bookkeeping pending; stage \| client info pending; stage \| preparing; stage \| client approval pending; stage \| review; stage \| ready to file; stage \| filed; stage \| completed; stage \| archived; blocker \| signature missing; blocker \| payment missing; blocker \| client documents missing; blocker \| archive needed; exception \| on hold; exception \| client requested revision; exception \| Urgent |
| 31 | GST Status | `cuqnbj1su8fmrl7` | `gst_status` | MultiSelect | text | no | no | stage \| not started | stage \| not started; stage \| bookkeeping pending; stage \| client info pending; stage \| preparing; stage \| client approval pending; stage \| ready to file; stage \| filed; stage \| completed; stage \| archived; blocker \| client documents missing; blocker \| sales summary missing; blocker \| payment missing; blocker \| archive needed; exception \| on hold; exception \| client requested revision; exception \| no filing required |
| 32 | PST Status | `cv2hweo04d4pfmv` | `pst_status` | MultiSelect | text | no | no | stage \| not started | stage \| not started; stage \| bookkeeping pending; stage \| client info pending; stage \| preparing; stage \| client approval pending; stage \| ready to file; stage \| filed; stage \| completed; stage \| archived; blocker \| client documents missing; blocker \| sales summary missing; blocker \| payment missing; blocker \| archive needed; exception \| on hold; exception \| client requested revision; exception \| no filing required |

### Views

| View | View ID | Type | Filters | Sorts |
| --- | --- | --- | --- | --- |
| Engagements | `vw44fndr3bavd58g` | 3 |  |  |
| Payroll - This Week v2 | `vwadifhhdegbecdj` | 3 | and Type eq Payroll; and Payroll Period End ge daysAgo; and Payroll Period End le daysFromNow; and Status neq Payroll \| Completed; and Status neq Payroll \| Archived | Payroll Period End asc |
| Workbench - GST Engagements | `vwxk6m2qwlmg79nb` | 3 | and Type eq GST; and Due Date ge daysAgo; and Due Date le daysFromNow; and GST Status neq stage \| completed; and GST Status neq stage \| archived | Needs Review desc; Priority asc; Due Date asc |
| Workbench - PST Engagements | `vwocqk0re3zu3mcy` | 3 | and Type eq PST; and Due Date ge daysAgo; and Due Date le daysFromNow; and PST Status neq stage \| completed; and PST Status neq stage \| archived | Needs Review desc; Priority asc; Due Date asc |
| Workbench - T2 Engagements | `vwawahbe6y4se9rc` | 3 | and Type eq T2; and Due Date le daysFromNow; and T2 Status neq stage \| completed; and T2 Status neq stage \| archived | Due Date asc; T2 Status asc; Name asc; Needs Review desc; Priority asc |

### Webhooks

Webhook destinations and headers are intentionally omitted.

| Hook | Hook ID | Active | Event | Operation |
| --- | --- | --- | --- | --- |
| engagement status to events | `hkz9j2qsk91g7gs5` | 1 | after | ['update'] |
| engagement business status completed to filed until | `hkjtl6qe8wbwqotn` | 1 | after | ['update'] |

## 4.4. Events

Table ID: `mntpl2d8ji03ac2`  
Live row count: **118**

### Fields

| Order | Display field | Field ID | Physical column | NocoDB type | DB type | PK | Required | Default | Options |
| ---: | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 1 | Id | `cjw1gyvzk2pdq4l` | `id` | ID | integer | yes | yes |  |  |
| 2 | CreatedAt | `c4ycq5cvqnpmhxw` | `created_at` | CreatedTime | datetime | no | no |  |  |
| 3 | UpdatedAt | `cbfb3w599votvk1` | `updated_at` | LastModifiedTime | datetime | no | no |  |  |
| 4 | nc_created_by | `cyce7azsi8aj07i` | `created_by` | CreatedBy | varchar | no | no |  |  |
| 5 | nc_updated_by | `ciek63091uantn2` | `updated_by` | LastModifiedBy | varchar | no | no |  |  |
| 6 | nc_order | `cyacbhgx5odfmur` | `nc_order` | Order | real | no | no |  |  |
| 7 | __nc_deleted | `cfpxxb0kou55f4t` | `__nc_deleted` | Deleted | boolean | no | no |  |  |
| 8 | Timestamp | `c3cphn8q468eyca` | `Timestamp` | Date | date | no | no |  |  |
| 9 | Engagement | `cpdpy9yn6rncq0b` | `Engagement` | SingleLineText | varchar | no | no |  |  |
| 10 | Event Type | `cmd4uuzcyoaej4v` | `Event_Type` | SingleSelect | text | no | no |  | Status Changed; Emailed; Document Operation; Client Reply; Notes; Manual Action; Manual Archive |
| 11 | Description | `cevntyc9o1zd9jg` | `Description` | LongText | text | no | no |  |  |
| 12 | Actor | `caf5ivrpccgnw4s` | `Actor` | SingleLineText | varchar | no | no |  |  |

### Views

| View | View ID | Type | Filters | Sorts |
| --- | --- | --- | --- | --- |
| Events | `vw08t0maqe3r8t6d` | 3 |  |  |
| Calendar | `vwdgn36wq4fwaio1` | 6 |  |  |

### Webhooks

No hooks were returned (`/api/v2/meta/tables/mntpl2d8ji03ac2/hooks`).

## 5. Current Non-Sensitive Distributions

### Companies

- `client status`: active=166
- `Is Payroll Client`: 0=113, 1=53
- `Client Type`: Annual=81, Monthly=79, (blank)=6
- `Pay Frequency`: (blank)=113, Monthly=29, Semi-monthly=15, Bi-weekly=7, Yearly=2

### People

- `client status`: active=112
- `Language`: Chinese=44, English=40, (blank)=22, Vietnamese=6

### Engagements

- `Type`: Payroll=148, T2=148, GST=112, PST=65
- `Status`: Not Started=318, Payroll \| Waiting Info=56, Payroll \| Completed=46, Payroll \| Archived=44, (blank)=6, Payroll \| Processing=2, Complete=1
- `GST Status`: (blank)=351, stage \| not started=121, stage \| completed=1
- `PST Status`: (blank)=398, stage \| not started=75
- `T2 Status`: (blank)=318, stage \| not started=141, stage \| client approval pending,blocker \| signature missing=6, stage \| review=4, stage \| filed=2, stage \| completed=1, stage \| preparing,blocker \| client documents missing=1
- `Priority`: P1 Overdue=154, (blank)=151, P4 OK=116, P0 Needs Review=27, P3 Due Soon=25
- `Source`: Excel Bootstrap=319, (blank)=151, Auto=3

### Events

- `Event Type`: Status Changed=71, Manual Archive=44, Manual Action=3

## 6. Integration Contract

- Business modules import `_lib/data_access.py`; they do not instantiate `NocoDBClient` directly.
- `_lib/connectors/nocodb_client.py` owns REST transport, authentication headers, timeout behavior, pagination, and API errors.
- `_lib/config/settings.py` owns environment-backed IDs and credentials. Never commit `_lib/config/.env`.
- Existing configured table IDs cover Companies, Engagements, and Events. People was discovered as `m6eek92z4tf1d17`; add a settings constant before a business module needs it rather than scattering the literal ID.
- Use NocoDB display titles for record payload keys, with exact casing and spacing.
- SingleSelect writes use the option title string. MultiSelect runtime serialization must not be inferred from metadata alone; perform a controlled non-production probe before depending on delimiter behavior.
- NocoDB metadata endpoints are administrative tooling, not a business-code interface.

Confirmed v2 API patterns used by platform tooling:

```text
GET   /api/v2/meta/tables/{table_id}
GET   /api/v2/meta/bases/{base_id}/tables
GET   /api/v2/meta/views/{view_id}/filters
GET   /api/v2/meta/views/{view_id}/sorts
GET   /api/v2/tables/{table_id}/records
POST  /api/v2/tables/{table_id}/records
PATCH /api/v2/tables/{table_id}/records
```

## 7. Automation And Safety Rules

- Money, CRA, customer email, invoices, and external communications always use Mode C: prepare automatically, require human confirmation.
- Events is append-only in platform intent. Automation actions must leave an event trail and must not rewrite historical events.
- Logs must not include tokens, SINs, customer contact details, or document contents.
- Schema changes belong in NocoDB product/admin operations. Do not hard-code complete schemas in business modules.
- Do not restore or patch legacy Baserow behavior. Baserow artifacts are migration history only.
- ExampleCo / RealEstate is a separate data model and must not be inserted into these four core tables.
- Production updates should be idempotent, bounded, verified by readback, and stopped before follow-on writes if verification fails.

## 8. Known Limitations And Data Quality

- Several cross-table references are plain text and therefore permit orphaned or mismatched values.
- Historical duplicate Companies were observed during the 2026-06-11 SQLite probe. Re-check only when deduplication is an approved task; do not merge automatically.
- The People `Language` select historically contained an anomalous option `1`; its presence in the live options is shown in the People field table above.
- The deployment uses SQLite. Older reports or snapshot sections describing the current backend as PostgreSQL are stale.
- NocoDB internal system fields (`nc_*`, `__nc_deleted`) are implementation fields. Other projects should not build business logic around them.
- Views are user-interface configuration and may evolve independently of the stable data-access contract.

## 9. Refresh Procedure

Refresh this document only when another project materially depends on current schema, view, hook, or count information:

1. Read `PROJECT_SNAPSHOT.md` and this document first.
2. Query only the known base/table/view IDs; do not recursively scan the UNC share.
3. Read metadata and aggregate counts only. Do not export customer rows into documentation.
4. Compare field IDs, titles, types, order, defaults, and select options.
5. Sanitize webhook destinations, headers, tokens, URLs, and all personal information.
6. Update the verification timestamp and record material changes in a short change note.

## 10. Source Evidence

- Live NocoDB v2 metadata and records APIs, read on the verification timestamp above.
- `_doc/datastore_discovery_probe_memory_2026-06-11.md` for Docker/SQLite physical deployment evidence.
- `_doc/nocodb_t2_gst_pst_status_fields_memory_2026-06-11.md` for tax status-field decisions.
- `_doc/tax_workbench_native_dynamic_filter_correction_2026-06-18.md` for current GST/PST/T2 rolling view filters.
- `payroll/_doc/nocodb_current_state_report_2026-06-03.md` for migration-era historical comparison only.
