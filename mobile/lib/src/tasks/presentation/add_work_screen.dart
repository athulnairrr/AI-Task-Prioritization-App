import 'package:flutter/material.dart';

import '../../design/tokens.dart';
import '../data/task_api_client.dart';
import 'task_detail_screen.dart';

const _examples = [
  'Finish the proposal by Friday',
  'Study system design for 2 hours',
  'Prepare Monday\'s presentation',
];

/// The primary "add work" entry point: one natural-language input, not a
/// traditional multi-field form. The full text becomes the task's title
/// (and raw_input, kept for AI prioritization context) -- structured
/// fields are available but tucked behind "Add details", not the default
/// path. Pushes straight into the task's detail screen afterward, where
/// "Prioritize with AI" is the obvious next action.
class AddWorkScreen extends StatefulWidget {
  const AddWorkScreen({super.key, TaskApiClient? taskApiClient}) : _injectedApi = taskApiClient;

  /// Injectable for tests -- defaults to a real [TaskApiClient].
  final TaskApiClient? _injectedApi;

  @override
  State<AddWorkScreen> createState() => _AddWorkScreenState();
}

class _AddWorkScreenState extends State<AddWorkScreen> {
  late final _api = widget._injectedApi ?? TaskApiClient();
  final _textController = TextEditingController();
  final _descriptionController = TextEditingController();
  final _minutesController = TextEditingController();
  DateTime? _dueAt;

  bool _showAdvanced = false;
  bool _saving = false;
  String? _error;

  @override
  void dispose() {
    _textController.dispose();
    _descriptionController.dispose();
    _minutesController.dispose();
    super.dispose();
  }

  Future<void> _pickDueDate() async {
    final picked = await showDatePicker(
      context: context,
      initialDate: _dueAt ?? DateTime.now(),
      firstDate: DateTime.now().subtract(const Duration(days: 1)),
      lastDate: DateTime.now().add(const Duration(days: 365 * 2)),
    );
    if (picked != null) setState(() => _dueAt = picked);
  }

  Future<void> _submit() async {
    final text = _textController.text.trim();
    if (text.isEmpty) {
      setState(() => _error = 'Tell us what you need to get done.');
      return;
    }
    setState(() {
      _saving = true;
      _error = null;
    });

    final minutesText = _minutesController.text.trim();
    final minutes = minutesText.isEmpty ? null : int.tryParse(minutesText);
    final description = _descriptionController.text.trim();

    try {
      final task = await _api.createTask(
        title: text,
        description: description.isEmpty ? null : description,
        dueAt: _dueAt,
        estimatedMinutes: minutes,
      );
      if (!mounted) return;
      Navigator.of(context).pushReplacement(
        MaterialPageRoute(builder: (_) => TaskDetailScreen(taskId: task.id, taskApiClient: _api)),
      );
    } on TaskApiException catch (e) {
      setState(() => _error = e.message);
    } catch (_) {
      setState(() => _error = 'Something went wrong. Please try again.');
    } finally {
      if (mounted) setState(() => _saving = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    final scheme = Theme.of(context).colorScheme;
    return Scaffold(
      appBar: AppBar(title: const Text('Add work')),
      body: SafeArea(
        child: ListView(
          padding: const EdgeInsets.all(Spacing.lg),
          children: [
            Text('What do you need to get done?', style: Theme.of(context).textTheme.headlineSmall),
            const SizedBox(height: Spacing.lg),
            TextField(
              controller: _textController,
              autofocus: true,
              maxLines: 3,
              minLines: 2,
              style: Theme.of(context).textTheme.titleMedium,
              decoration: const InputDecoration(
                hintText: 'e.g. "Finish the client proposal by Friday"',
              ),
            ),
            if (_error != null) ...[
              const SizedBox(height: Spacing.sm),
              Text(_error!, style: TextStyle(color: scheme.error)),
            ],
            const SizedBox(height: Spacing.md),
            Text('Examples', style: Theme.of(context).textTheme.labelLarge?.copyWith(color: scheme.onSurfaceVariant)),
            const SizedBox(height: Spacing.sm),
            Wrap(
              spacing: Spacing.sm,
              runSpacing: Spacing.sm,
              children: [
                for (final example in _examples)
                  ActionChip(
                    label: Text(example),
                    onPressed: () => setState(() => _textController.text = example),
                  ),
              ],
            ),
            const SizedBox(height: Spacing.xl),
            TextButton.icon(
              onPressed: () => setState(() => _showAdvanced = !_showAdvanced),
              icon: Icon(_showAdvanced ? Icons.expand_less : Icons.expand_more),
              label: const Text('Add details (optional)'),
            ),
            if (_showAdvanced) ...[
              const SizedBox(height: Spacing.sm),
              TextField(
                controller: _descriptionController,
                maxLines: 3,
                decoration: const InputDecoration(labelText: 'Description'),
              ),
              const SizedBox(height: Spacing.md),
              TextField(
                controller: _minutesController,
                keyboardType: TextInputType.number,
                decoration: const InputDecoration(labelText: 'Estimated minutes'),
              ),
              const SizedBox(height: Spacing.md),
              ListTile(
                contentPadding: EdgeInsets.zero,
                title: Text(_dueAt == null
                    ? 'No due date'
                    : 'Due ${_dueAt!.toLocal().toString().split(' ').first}'),
                trailing: Wrap(
                  children: [
                    TextButton(onPressed: _pickDueDate, child: const Text('Pick date')),
                    if (_dueAt != null)
                      IconButton(icon: const Icon(Icons.clear), onPressed: () => setState(() => _dueAt = null)),
                  ],
                ),
              ),
            ],
            const SizedBox(height: Spacing.xl),
            FilledButton(
              onPressed: _saving ? null : _submit,
              child: _saving
                  ? const SizedBox(height: 18, width: 18, child: CircularProgressIndicator(strokeWidth: 2))
                  : const Text('Add task'),
            ),
          ],
        ),
      ),
    );
  }
}
