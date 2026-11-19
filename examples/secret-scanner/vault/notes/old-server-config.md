# Server notes

## Overview

The staging environment mirrors production at a smaller scale. Deployments run
nightly and the dashboards are reviewed every Monday.

## Old Server Config

This was the setup before the migration. Delete once everyone has moved over.

```
aws_access_key_id = AKIAIOSFODNN7EXAMPLE
db_password = hunter2hunter2
DATABASE_URL = postgres://admin:s3cretPassw0rd@db1.corp.example:5432/app
```

## Cleanup checklist

- Rotate old credentials.
- Remove unused load balancers.
