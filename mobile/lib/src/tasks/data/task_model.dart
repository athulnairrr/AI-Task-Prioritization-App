enum TaskStatus {
  pending,
  inProgress,
  done,
  cancelled;

  static TaskStatus fromApi(String value) {
    switch (value) {
      case 'pending':
        return TaskStatus.pending;
      case 'in_progress':
        return TaskStatus.inProgress;
      case 'done':
        return TaskStatus.done;
      case 'cancelled':
        return TaskStatus.cancelled;
      default:
        throw ArgumentError('Unknown task status: $value');
    }
  }

  String toApi() {
    switch (this) {
      case TaskStatus.pending:
        return 'pending';
      case TaskStatus.inProgress:
        return 'in_progress';
      case TaskStatus.done:
        return 'done';
      case TaskStatus.cancelled:
        return 'cancelled';
    }
  }

  String get label {
    switch (this) {
      case TaskStatus.pending:
        return 'Pending';
      case TaskStatus.inProgress:
        return 'In progress';
      case TaskStatus.done:
        return 'Done';
      case TaskStatus.cancelled:
        return 'Cancelled';
    }
  }
}

/// Mirrors the backend's `TaskOut` schema (see backend/app/schemas/task.py).
class Task {
  const Task({
    required this.id,
    required this.tenantId,
    required this.createdBy,
    required this.title,
    required this.status,
    this.description,
    this.rawInput,
    this.dueAt,
    this.estimatedMinutes,
    required this.createdAt,
    required this.updatedAt,
  });

  final String id;
  final String tenantId;
  final String createdBy;
  final String title;
  final String? description;
  final String? rawInput;
  final TaskStatus status;
  final DateTime? dueAt;
  final int? estimatedMinutes;
  final DateTime createdAt;
  final DateTime updatedAt;

  factory Task.fromJson(Map<String, dynamic> json) {
    return Task(
      id: json['id'] as String,
      tenantId: json['tenant_id'] as String,
      createdBy: json['created_by'] as String,
      title: json['title'] as String,
      description: json['description'] as String?,
      rawInput: json['raw_input'] as String?,
      status: TaskStatus.fromApi(json['status'] as String),
      dueAt: json['due_at'] == null ? null : DateTime.parse(json['due_at'] as String),
      estimatedMinutes: json['estimated_minutes'] as int?,
      createdAt: DateTime.parse(json['created_at'] as String),
      updatedAt: DateTime.parse(json['updated_at'] as String),
    );
  }
}

/// Mirrors the backend's `PrioritizedTaskOut` schema
/// (backend/app/schemas/task.py) -- a task joined with its latest AI
/// result, if any. Powers the Today screen's "high priority" list and the
/// Prioritized Tasks screen without an N+1 fetch per task.
class PrioritizedTask {
  const PrioritizedTask({
    required this.id,
    required this.title,
    this.description,
    required this.status,
    this.dueAt,
    this.estimatedMinutes,
    required this.createdAt,
    this.priorityScore,
    this.confidenceScore,
    this.urgency,
    this.importance,
    this.category,
    this.effortEstimateMinutes,
    this.reasoning,
  });

  final String id;
  final String title;
  final String? description;
  final TaskStatus status;
  final DateTime? dueAt;
  final int? estimatedMinutes;
  final DateTime createdAt;
  final double? priorityScore;
  final double? confidenceScore;
  final String? urgency;
  final String? importance;
  final String? category;
  final int? effortEstimateMinutes;
  final String? reasoning;

  bool get isPrioritized => priorityScore != null;

  factory PrioritizedTask.fromJson(Map<String, dynamic> json) {
    return PrioritizedTask(
      id: json['id'] as String,
      title: json['title'] as String,
      description: json['description'] as String?,
      status: TaskStatus.fromApi(json['status'] as String),
      dueAt: json['due_at'] == null ? null : DateTime.parse(json['due_at'] as String),
      estimatedMinutes: json['estimated_minutes'] as int?,
      createdAt: DateTime.parse(json['created_at'] as String),
      priorityScore: (json['priority_score'] as num?)?.toDouble(),
      confidenceScore: (json['confidence_score'] as num?)?.toDouble(),
      urgency: json['urgency'] as String?,
      importance: json['importance'] as String?,
      category: json['category'] as String?,
      effortEstimateMinutes: json['effort_estimate_minutes'] as int?,
      reasoning: json['reasoning'] as String?,
    );
  }
}

/// Mirrors the backend's `TaskAiResultOut` schema (backend/app/schemas/ai.py).
class TaskAiResult {
  const TaskAiResult({
    required this.id,
    required this.taskId,
    required this.model,
    this.category,
    this.urgency,
    this.importance,
    this.priorityScore,
    this.confidenceScore,
    this.effortEstimateMinutes,
    this.reasoning,
    required this.createdAt,
  });

  final String id;
  final String taskId;
  final String model;
  final String? category;
  final String? urgency;
  final String? importance;
  final double? priorityScore;
  final double? confidenceScore;
  final int? effortEstimateMinutes;
  final String? reasoning;
  final DateTime createdAt;

  factory TaskAiResult.fromJson(Map<String, dynamic> json) {
    return TaskAiResult(
      id: json['id'] as String,
      taskId: json['task_id'] as String,
      model: json['model'] as String,
      category: json['category'] as String?,
      urgency: json['urgency'] as String?,
      importance: json['importance'] as String?,
      priorityScore: (json['priority_score'] as num?)?.toDouble(),
      confidenceScore: (json['confidence_score'] as num?)?.toDouble(),
      effortEstimateMinutes: json['effort_estimate_minutes'] as int?,
      reasoning: json['reasoning'] as String?,
      createdAt: DateTime.parse(json['created_at'] as String),
    );
  }
}
