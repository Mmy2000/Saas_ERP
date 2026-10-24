# ADR-022: Who may change a user who belongs to several workspaces

- Status: **Accepted** (2026-10-25). Relates to ADR-004 in `PROJECT_PLAN.md`.
- Date: 2026-10-24
- Deciders: product owner (Mahmoud)

## Context

`User` is global: one row per person, across every workspace and the console. `Membership`
links a user to a workspace (ADR-004). The audit (finding S1, [audit](../audit/2026-10-engineering-audit.md#security-and-tenant-isolation))
found:

- `apps/iam/services.py`: `update_member` changes the global user's **email**, and
  `set_member_password` changes the global user's **password**. The only guard,
  `_ensure_can_grant`, looks at the user's roles **in the current workspace**. So the admin of
  workspace A can take over that person's access to workspace B, or to the console if they are
  platform staff.
- `apps/platform/tenants/services.py:80-87`: provisioning a workspace whose owner email already
  exists reuses that user and **ignores the password typed** in the console. Someone who created
  a user with that email earlier, in their own workspace, knows its password.
- `_validate_member` reveals that an email exists in another workspace. A phone number taken
  elsewhere causes a 500.

## Decision

1. A workspace may change the password or email of a user **only if that user has no other
   active membership and is not platform staff**. Otherwise it gets a clear refusal, and only
   the user can change those fields, from their own account.
2. Provisioning never silently reuses an existing user. If the email exists, staff choose,
   explicitly: either link the existing user (who keeps their password, and is told), or use
   another email.
3. Platform staff accounts can't hold workspace memberships. Staff use separate accounts for
   test workspaces.
4. An email or phone already taken elsewhere gives one neutral message (no 500, no disclosure).

## Alternatives considered

- **One user per workspace** (no global identity). This is the simplest isolation, but a person
  working for two shops needs two accounts. It changes ADR-004 and needs a data migration for
  existing shared users.
- **Keep global users and add e-mail verification for every sensitive change.** Stronger, but
  it needs reliable e-mail for every member; many shop staff sign in by username only.

## How it is built

- **The count.** A workspace can't see other workspaces' memberships, by design. So the
  database counts them: `User.membership_count`, kept by the trigger `iam_membership_count` on
  `iam_membership`, and back-filled one tenant at a time (`apps/iam/migrations/0009`).
- **The refusal.** `apps/iam/services.py::_ensure_not_shared` refuses `set_member_password`,
  and refuses `update_member` when the email or phone changes, for staff or for a count above 1
  (`IAM_SHARED_LOGIN`).
- **Provisioning.**
  - `ProvisionTenantCommand.link_existing_owner` must be set to add a workspace to an existing
    login. In the console it's the box "The owner already has a Gweb login: add this workspace
    to it"; in `create_tenant`, `--link-existing-owner`.
  - Refused for a staff login (`TENANT_OWNER_IS_STAFF`), without the flag
    (`TENANT_OWNER_EXISTS`), or with a password (`TENANT_OWNER_PASSWORD`).
- **Not done.**
  - The email message on adding a member still says the email exists (point 4 covers the
    phone only); a neutral wording would confuse shop admins for little gain.
  - Point 3 is enforced where staff logins could be handed over (provisioning). A staff login
    that already holds a membership is protected by the refusal, not removed.
  - There is no self-service "forgot password" flow yet: until there is, platform staff reset a
    shared login's password with `manage.py changepassword <email>`.
- **Tests:** `apps/iam/tests/test_shared_logins.py`, and `tests/test_console.py::test_create_a_client`.

## Consequences

- Closes the takeover path without a data migration.
- An owner can no longer reset the password of a member who also works for another shop. That
  member resets it themselves (a "forgot password" flow is then needed), or platform staff do.
- Tested: the refusal from a workspace for a login also used in another, the refusal for a
  staff login, provisioning with an existing email, and the duplicate phone.
