"""AWS Lambda handler for dockcheck hello example."""

from __future__ import annotations

import json


def handler(event, context):
    return {
        "statusCode": 200,
        "body": json.dumps({"message": "Hello from dockcheck!"}),
        "headers": {"Content-Type": "application/json"},
    }
