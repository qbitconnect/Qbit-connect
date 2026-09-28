# QBIT GROWTH OS — STITCH SCREEN TO ROUTE MAPPING
**Project ID:** 11551070906530769156  
**Company:** QbitPro India Pvt Ltd.  
**Rule:** Preserve 100% of approved Stitch UI design, markup, styles, and responsive components.

---

## 1. Authentication & Onboarding Module

| Stitch Screen Folder | Frontend Route | Backend API Endpoint | Primary Model / Entity | Permissions Required |
|---|---|---|---|---|
| `login_qbit_growth_os` | `/login` | `POST /api/v1/auth/login` | `User`, `UserSession` | Public |
| `two_factor_authentication_qbit_growth_os` | `/login/2fa` | `POST /api/v1/auth/2fa/verify` | `User`, `UserSession` | Pre-auth session |
| `forgot_password_qbit_growth_os` | `/forgot-password` | `POST /api/v1/auth/password/reset-request` | `User` | Public |
| `create_new_password_qbit_growth_os` | `/reset-password` | `POST /api/v1/auth/password/reset-confirm` | `User` | Public (token required) |
| `workspace_selection_qbit_growth_os` | `/workspace/select` | `GET /api/v1/teams`, `POST /api/v1/sessions/workspace` | `Organization`, `Team` | Authenticated |

---

## 2. Command Center & Intelligence Module

| Stitch Screen Folder | Frontend Route | Backend API Endpoint | Primary Model / Entity | Permissions Required |
|---|---|---|---|---|
| `main_dashboard_ceo_command_center` | `/` or `/dashboard` | `GET /api/v1/analytics/dashboard`, `GET /health` | `MetricCache`, `ScrapeJob`, `Lead` | `dashboard.view` |
| `lead_analytics_qbit_growth_os` | `/analytics/leads` | `GET /api/v1/analytics/leads` | `Lead`, `LeadBatch` | `analytics.view` |
| `campaign_analytics_qbit_growth_os` | `/analytics/campaigns` | `GET /api/v1/analytics/campaigns` | `Campaign`, `CampaignRecipient` | `analytics.view` |

---

## 3. Scraper Marketplace & Runner Ecosystem

| Stitch Screen Folder | Frontend Route | Backend API Endpoint | Primary Model / Entity | Permissions Required |
|---|---|---|---|---|
| `scraper_marketplace_qbit_growth_os` / `scraper_marketplace` | `/scrapers` | `GET /api/v1/scrapers`, `GET /api/v1/actors` | `Scraper`, `ActorDefinition` | `scraping.view` |
| `scraper_detail_google_maps_business_scraper` | `/scrapers/{slug}` | `GET /api/v1/scrapers/{slug}` | `Scraper`, `ActorTask` | `scraping.view` |
| `configure_google_maps_scraper_easy_form_qbit_growth_os` | `/scrapers/{slug}/configure` | `GET /api/v1/scrapers/{slug}/schema` | `ScraperInputSchema` | `scraping.run` |
| `scraper_input_configuration_step_by_step_qbit_growth_os` | `/scrapers/{slug}/configure-wizard` | `GET /api/v1/scrapers/{slug}/steps` | `ScraperStepConfig` | `scraping.run` |
| `data_fields_selection_configure_google_maps_scraper_1` & `_2` | `/scrapers/{slug}/fields` | `GET /api/v1/scrapers/{slug}/fields` | `ScraperFieldSelection` | `scraping.run` |
| `advanced_settings_json_options_google_maps_scraper` | `/scrapers/{slug}/advanced` | `POST /api/v1/scrapers/validate-json` | `ActorTaskInput` | `scraping.manage` |
| `run_preview_confirmation_google_maps_scraper` & `run_confirmation_google_maps_scraper` | `/scrapers/{slug}/confirm` | `POST /api/v1/scrape-jobs` | `ScrapeJob`, `ActorRun` | `scraping.run` |
| `live_run_console_qbit_growth_os_1` & `_2` | `/runs/{run_id}/console` | `GET /api/v1/scrape-jobs/{id}/logs`, `GET /api/v1/scrape-jobs/{id}` | `ScrapeJobLog`, `ActorRun` | `scraping.view` |
| `run_completed_summary_google_maps_scraper` | `/runs/{run_id}/summary` | `GET /api/v1/scrape-jobs/{id}/summary` | `ScrapeJobResult` | `scraping.view` |

---

## 4. Lead CRM & Data Operations Module

| Stitch Screen Folder | Frontend Route | Backend API Endpoint | Primary Model / Entity | Permissions Required |
|---|---|---|---|---|
| `data_results_table_google_maps_scraper_1`, `_2`, `_3` | `/leads` or `/runs/{id}/results` | `GET /api/v1/leads`, `GET /api/v1/datasets/{id}` | `Lead`, `Dataset` | `leads.view` |
| `lead_detail_drawer_the_royal_palace_hotel_1`, `_2`, `_3` | `/leads/{id}/drawer` (slide-out) | `GET /api/v1/leads/{id}`, `GET /api/v1/leads/{id}/activity` | `Lead`, `LeadActivity`, `LeadNote` | `leads.view` |
| `import_leads_csv_excel_mapper` | `/leads/import` | `POST /api/v1/leads/import/upload`, `POST /api/v1/leads/import/commit` | `ImportBatch`, `Lead` | `leads.import` |
| `deduplicate_merge_leads_qbit_growth_os` | `/leads/deduplicate` | `GET /api/v1/leads/duplicates`, `POST /api/v1/leads/merge` | `Lead`, `LeadMergeHistory` | `leads.merge` |
| `lead_lists_management_qbit_growth_os` | `/leads/lists` | `GET /api/v1/leads/views`, `POST /api/v1/leads/views` | `SavedLeadView`, `LeadTag` | `leads.manage_views` |

---

## 5. Marketing, Outbound & Automation Module

| Stitch Screen Folder | Frontend Route | Backend API Endpoint | Primary Model / Entity | Permissions Required |
|---|---|---|---|---|
| `campaigns_dashboard_qbit_growth_os` | `/campaigns` | `GET /api/v1/campaigns` | `Campaign`, `CampaignStats` | `campaigns.view` |
| `create_campaign_step_by_step_qbit_growth_os` | `/campaigns/new` | `POST /api/v1/campaigns` | `Campaign`, `Audience` | `campaigns.create` |
| `campaign_content_editor_qbit_growth_os` | `/campaigns/{id}/editor` | `GET/PUT /api/v1/templates/{id}` | `Template`, `TemplateVariable` | `campaigns.edit` |
| `campaign_review_launch_qbit_growth_os` | `/campaigns/{id}/launch` | `POST /api/v1/campaigns/{id}/launch` | `Campaign`, `SendingAccount` | `campaigns.launch` |
| `automation_workflow_builder_qbit_growth_os` | `/automation` | `GET/POST /api/v1/automation/workflows` | `Workflow`, `WorkflowTrigger`, `WorkflowAction` | `automation.manage` |
| `ai_research_assistant_qbit_growth_os` | `/ai-assistant` | `POST /api/v1/orchestration/research` | `OrchestrationTask` | `ai.research` |

---

## 6. Administration, Security & Governance Module

| Stitch Screen Folder | Frontend Route | Backend API Endpoint | Primary Model / Entity | Permissions Required |
|---|---|---|---|---|
| `integrations_connected_accounts_qbit_growth_os` | `/connections` | `GET /api/v1/connections`, `POST /api/v1/connections` | `Connection`, `SendingAccount` | `connections.view` / `manage` |
| `team_management_qbit_growth_os` | `/admin/teams` | `GET /api/v1/teams`, `POST /api/v1/invitations` | `Team`, `TeamMember`, `Invitation` | `teams.view` / `invitations.create` |
| `role_permissions_qbit_growth_os` | `/admin/roles` | `GET /api/v1/roles`, `PUT /api/v1/roles/{id}` | `Role`, `Permission`, `RolePermission` | `roles.view` / `roles.manage` |
| `security_audit_logs_qbit_growth_os` | `/admin/audit` | `GET /api/v1/admin/audit` | `AuditEvent` | `audit.view` |
| `profile_settings_qbit_growth_os` | `/settings` | `GET /api/v1/users/me`, `PUT /api/v1/users/me` | `User`, `ApiKey` | Authenticated |
