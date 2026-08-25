/// Mirrors the backend's scheduling schemas (backend/app/schemas/scheduling.py).
class ProposedScheduleItem {
  const ProposedScheduleItem({
    required this.taskId,
    required this.title,
    required this.start,
    required this.end,
    required this.priorityScore,
    required this.score,
    required this.reason,
  });

  final String taskId;
  final String title;
  final DateTime start;
  final DateTime end;
  final double priorityScore;
  final double score;
  final String reason;

  factory ProposedScheduleItem.fromJson(Map<String, dynamic> json) {
    return ProposedScheduleItem(
      taskId: json['task_id'] as String,
      title: json['title'] as String,
      start: DateTime.parse(json['start'] as String),
      end: DateTime.parse(json['end'] as String),
      priorityScore: (json['priority_score'] as num).toDouble(),
      score: (json['score'] as num).toDouble(),
      reason: json['reason'] as String,
    );
  }
}

class UnscheduledTask {
  const UnscheduledTask({required this.taskId, required this.title, required this.reason});

  final String taskId;
  final String title;
  final String reason;

  factory UnscheduledTask.fromJson(Map<String, dynamic> json) {
    return UnscheduledTask(
      taskId: json['task_id'] as String,
      title: json['title'] as String,
      reason: json['reason'] as String,
    );
  }
}

class ScheduleProposal {
  const ScheduleProposal({required this.scheduled, required this.unscheduled});

  final List<ProposedScheduleItem> scheduled;
  final List<UnscheduledTask> unscheduled;

  factory ScheduleProposal.fromJson(Map<String, dynamic> json) {
    return ScheduleProposal(
      scheduled: (json['scheduled'] as List<dynamic>)
          .map((e) => ProposedScheduleItem.fromJson(e as Map<String, dynamic>))
          .toList(),
      unscheduled: (json['unscheduled'] as List<dynamic>)
          .map((e) => UnscheduledTask.fromJson(e as Map<String, dynamic>))
          .toList(),
    );
  }
}

enum AppliedItemStatus {
  created,
  alreadyApplied,
  failed;

  static AppliedItemStatus fromApi(String value) {
    switch (value) {
      case 'created':
        return AppliedItemStatus.created;
      case 'already_applied':
        return AppliedItemStatus.alreadyApplied;
      default:
        return AppliedItemStatus.failed;
    }
  }
}

/// Mirrors the backend's AppliedItemResult schema.
class AppliedItemResult {
  const AppliedItemResult({
    required this.taskId,
    required this.status,
    this.googleEventId,
    this.start,
    this.end,
    this.reason,
  });

  final String taskId;
  final AppliedItemStatus status;
  final String? googleEventId;
  final DateTime? start;
  final DateTime? end;
  final String? reason;

  factory AppliedItemResult.fromJson(Map<String, dynamic> json) {
    return AppliedItemResult(
      taskId: json['task_id'] as String,
      status: AppliedItemStatus.fromApi(json['status'] as String),
      googleEventId: json['google_event_id'] as String?,
      start: json['start'] == null ? null : DateTime.parse(json['start'] as String),
      end: json['end'] == null ? null : DateTime.parse(json['end'] as String),
      reason: json['reason'] as String?,
    );
  }
}

class ScheduleApplyResult {
  const ScheduleApplyResult({
    required this.created,
    required this.alreadyApplied,
    required this.failed,
    required this.results,
  });

  final int created;
  final int alreadyApplied;
  final int failed;
  final List<AppliedItemResult> results;

  factory ScheduleApplyResult.fromJson(Map<String, dynamic> json) {
    return ScheduleApplyResult(
      created: json['created'] as int,
      alreadyApplied: json['already_applied'] as int,
      failed: json['failed'] as int,
      results: (json['results'] as List<dynamic>)
          .map((e) => AppliedItemResult.fromJson(e as Map<String, dynamic>))
          .toList(),
    );
  }
}

/// Mirrors the backend's ScheduleItemOut schema (GET /tasks/schedule/items)
/// -- one applied schedule item in a date range, with just enough joined
/// in (title, priority, Calendar mapping status) to render the Today and
/// Calendar screens without stitching multiple calls together.
class ScheduleItem {
  const ScheduleItem({
    required this.scheduleItemId,
    required this.taskId,
    required this.title,
    required this.startsAt,
    required this.endsAt,
    required this.status,
    required this.needsAttention,
    this.attentionReason,
    this.googleEventId,
    this.syncStatus,
    this.priorityScore,
  });

  final String scheduleItemId;
  final String taskId;
  final String title;
  final DateTime startsAt;
  final DateTime endsAt;
  final String status;
  final bool needsAttention;
  final String? attentionReason;
  final String? googleEventId;
  final String? syncStatus;
  final double? priorityScore;

  Duration get duration => endsAt.difference(startsAt);

  factory ScheduleItem.fromJson(Map<String, dynamic> json) {
    return ScheduleItem(
      scheduleItemId: json['schedule_item_id'] as String,
      taskId: json['task_id'] as String,
      title: json['title'] as String,
      startsAt: DateTime.parse(json['starts_at'] as String),
      endsAt: DateTime.parse(json['ends_at'] as String),
      status: json['status'] as String,
      needsAttention: json['needs_attention'] as bool? ?? false,
      attentionReason: json['attention_reason'] as String?,
      googleEventId: json['google_event_id'] as String?,
      syncStatus: json['sync_status'] as String?,
      priorityScore: (json['priority_score'] as num?)?.toDouble(),
    );
  }
}

/// Mirrors the backend's NeedsAttentionItemOut schema -- a previously-
/// applied schedule item whose Google Calendar event was deleted
/// externally (Phase 7 two-way sync). Never auto-recreated.
class NeedsAttentionItem {
  const NeedsAttentionItem({
    required this.taskId,
    required this.scheduleItemId,
    required this.title,
    this.reason,
    required this.startsAt,
    required this.endsAt,
  });

  final String taskId;
  final String scheduleItemId;
  final String title;
  final String? reason;
  final DateTime startsAt;
  final DateTime endsAt;

  factory NeedsAttentionItem.fromJson(Map<String, dynamic> json) {
    return NeedsAttentionItem(
      taskId: json['task_id'] as String,
      scheduleItemId: json['schedule_item_id'] as String,
      title: json['title'] as String,
      reason: json['reason'] as String?,
      startsAt: DateTime.parse(json['starts_at'] as String),
      endsAt: DateTime.parse(json['ends_at'] as String),
    );
  }
}
