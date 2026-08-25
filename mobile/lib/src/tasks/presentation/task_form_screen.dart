import 'package:flutter/material.dart';

import '../data/task_api_client.dart';
import '../data/task_model.dart';
import 'ai_prioritization_section.dart';

/// Create/edit form for a single task. Pass `task` to edit, omit to create.
/// Returns `true` via Navigator.pop when a change was saved, so the list
/// screen knows to refresh.
class TaskFormScreen extends StatefulWidget {
  const TaskFormScreen({super.key, this.task});

  final Task? task;

  bool get isEditing => task != null;

  @override
  State<TaskFormScreen> createState() => _TaskFormScreenState();
}

class _TaskFormScreenState extends State<TaskFormScreen> {
  final _formKey = GlobalKey<FormState>();
  final _api = TaskApiClient();

  late final TextEditingController _titleController;
  late final TextEditingController _descriptionController;
  late final TextEditingController _estimatedMinutesController;
  DateTime? _dueAt;

  bool _saving = false;
  String? _error;

  @override
  void initState() {
    super.initState();
    final task = widget.task;
    _titleController = TextEditingController(text: task?.title ?? '');
    _descriptionController = TextEditingController(text: task?.description ?? '');
    _estimatedMinutesController =
        TextEditingController(text: task?.estimatedMinutes?.toString() ?? '');
    _dueAt = task?.dueAt;
  }

  @override
  void dispose() {
    _titleController.dispose();
    _descriptionController.dispose();
    _estimatedMinutesController.dispose();
    super.dispose();
  }

  Future<void> _pickDueDate() async {
    final picked = await showDatePicker(
      context: context,
      initialDate: _dueAt ?? DateTime.now(),
      firstDate: DateTime.now().subtract(const Duration(days: 365)),
      lastDate: DateTime.now().add(const Duration(days: 365 * 2)),
    );
    if (picked != null) setState(() => _dueAt = picked);
  }

  Future<void> _save() async {
    if (!_formKey.currentState!.validate()) return;
    setState(() {
      _saving = true;
      _error = null;
    });

    final title = _titleController.text.trim();
    final description = _descriptionController.text.trim();
    final estimatedText = _estimatedMinutesController.text.trim();
    final estimatedMinutes = estimatedText.isEmpty ? null : int.tryParse(estimatedText);

    try {
      if (widget.isEditing) {
        await _api.updateTask(
          widget.task!.id,
          title: title,
          description: description.isEmpty ? null : description,
          dueAt: _dueAt,
          clearDueAt: _dueAt == null,
          estimatedMinutes: estimatedMinutes,
        );
      } else {
        await _api.createTask(
          title: title,
          description: description.isEmpty ? null : description,
          dueAt: _dueAt,
          estimatedMinutes: estimatedMinutes,
        );
      }
      if (mounted) Navigator.of(context).pop(true);
    } on TaskApiException catch (e) {
      setState(() => _error = e.message);
    } catch (_) {
      setState(() => _error = 'Something went wrong. Please try again.');
    } finally {
      if (mounted) setState(() => _saving = false);
    }
  }

  Future<void> _delete() async {
    setState(() => _saving = true);
    try {
      await _api.deleteTask(widget.task!.id);
      if (mounted) Navigator.of(context).pop(true);
    } on TaskApiException catch (e) {
      setState(() {
        _error = e.message;
        _saving = false;
      });
    }
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(
        title: Text(widget.isEditing ? 'Edit task' : 'New task'),
        actions: [
          if (widget.isEditing)
            IconButton(
              icon: const Icon(Icons.delete_outline),
              tooltip: 'Delete task',
              onPressed: _saving ? null : _delete,
            ),
        ],
      ),
      body: Form(
        key: _formKey,
        child: ListView(
          padding: const EdgeInsets.all(16),
          children: [
            if (_error != null) ...[
              Text(_error!, style: TextStyle(color: Theme.of(context).colorScheme.error)),
              const SizedBox(height: 12),
            ],
            TextFormField(
              controller: _titleController,
              decoration: const InputDecoration(labelText: 'Title'),
              textInputAction: TextInputAction.next,
              validator: (value) =>
                  (value == null || value.trim().isEmpty) ? 'Title is required' : null,
            ),
            const SizedBox(height: 12),
            TextFormField(
              controller: _descriptionController,
              decoration: const InputDecoration(labelText: 'Description (optional)'),
              maxLines: 3,
            ),
            const SizedBox(height: 12),
            TextFormField(
              controller: _estimatedMinutesController,
              decoration: const InputDecoration(labelText: 'Estimated minutes (optional)'),
              keyboardType: TextInputType.number,
              validator: (value) {
                if (value == null || value.trim().isEmpty) return null;
                final n = int.tryParse(value.trim());
                if (n == null || n <= 0) return 'Enter a positive number of minutes';
                return null;
              },
            ),
            const SizedBox(height: 12),
            ListTile(
              contentPadding: EdgeInsets.zero,
              title: Text(_dueAt == null
                  ? 'No due date'
                  : 'Due ${_dueAt!.toLocal().toString().split(' ').first}'),
              trailing: Wrap(
                children: [
                  TextButton(onPressed: _pickDueDate, child: const Text('Pick date')),
                  if (_dueAt != null)
                    IconButton(
                      icon: const Icon(Icons.clear),
                      onPressed: () => setState(() => _dueAt = null),
                    ),
                ],
              ),
            ),
            const SizedBox(height: 24),
            FilledButton(
              onPressed: _saving ? null : _save,
              child: _saving
                  ? const SizedBox(
                      height: 18, width: 18, child: CircularProgressIndicator(strokeWidth: 2))
                  : Text(widget.isEditing ? 'Save changes' : 'Create task'),
            ),
            if (widget.isEditing) ...[
              const SizedBox(height: 24),
              AiPrioritizationSection(taskId: widget.task!.id),
            ],
          ],
        ),
      ),
    );
  }
}
