# Okavango OPR and Material Request Release Candidate

## Release scope

This release candidate contains:

- Operational Payment Request backend changes
- Company-specific payment account mapping
- Payment-method account enforcement
- Existing independent approval protections
- Existing cost-center validation
- Sanitized OPR/MR configuration metadata
- Read-only production preflight

## Existing implementation

GitHub main already contains:

- Material Request stock automation
- Material Request list indicator
- OPR document hooks
- OPR fixture registration
- Initial OPR configuration seeding

These implementations must not be duplicated.

## Configuration package

41 configuration records are packaged.

They contain no business transactions.

The QA-only installer is intentionally excluded.

## Deployment limitations

This commit is a release candidate, not authorization
to install or activate changes on production.

A production-specific upgrade operation has not
yet been implemented or approved.

Before production deployment:

1. Identify the actual live ERPNext site and app revision.
2. Back up the complete production database and files.
3. Compare existing OPR/MR configuration records.
4. Resolve conflicting workflows and customizations.
5. Obtain Finance approval for optional payment evidence.
6. Confirm payment accounts for each company and method.
7. Approve any changes to COO self-approval policy.
8. Keep automatic stock posting disabled until approved.
9. Apply only reviewed configuration changes.
10. Verify deployment and retain a rollback procedure.

## Safety

- No ERPNext core edits.
- No transaction deletion or replacement.
- No automatic workflow activation.
- No unrestricted configuration overwrite.
- No QA-only installer execution on production.

## Status

Release candidate prepared for review.

Production installation: NOT PERFORMED.
