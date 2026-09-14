#!/usr/bin/env python3
"""Verify that WorkloadAnalysisService imports correctly."""

import sys

try:
    from personal_deadline_management_agent.services.workload_analysis_service import WorkloadAnalysisService
    from personal_deadline_management_agent.schemas.workload import (
        WorkloadAnalysisResult,
        DeadlineCollision,
        BusyDayWarning,
        TaskSummary,
    )
    from personal_deadline_management_agent.models import Task, TaskPriority, TaskStatus

    print("✓ All imports successful")

    # Verify the service can be instantiated
    service = WorkloadAnalysisService()
    print("✓ Service instantiation successful")

    # Verify the analyze method exists
    assert hasattr(service, 'analyze')
    print("✓ Service has analyze method")

    # Test empty analysis
    result = service.analyze([])
    assert isinstance(result, WorkloadAnalysisResult)
    assert result.total_tasks == 0
    assert result.deadline_collisions == []
    assert result.busy_days == []
    assert result.recommended_order == []
    assert "no active tasks" in result.explanation.lower()
    print("✓ Empty analysis works correctly")

    print("\n✅ All verifications passed!")
    sys.exit(0)

except Exception as e:
    print(f"❌ Error: {e}")
    import traceback
    traceback.print_exc()
    sys.exit(1)
