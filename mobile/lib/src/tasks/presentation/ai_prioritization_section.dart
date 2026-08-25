import 'package:flutter/material.dart';

import '../data/task_api_client.dart';
import '../data/task_model.dart';

enum _AiLoadStatus { loadingExisting, running, loaded, error }

/// "Prioritize with AI" card for the task form. Loads any existing result
/// on open (a plain GET, no Gemini call) and only calls Gemini when the
/// user explicitly taps the button -- never automatically.
class AiPrioritizationSection extends StatefulWidget {
  const AiPrioritizationSection({super.key, required this.taskId, TaskApiClient? apiClient})
      : _injectedApi = apiClient;

  final String taskId;
  // Injectable for tests -- defaults to a real [TaskApiClient].
  final TaskApiClient? _injectedApi;

  @override
  State<AiPrioritizationSection> createState() => _AiPrioritizationSectionState();
}

class _AiPrioritizationSectionState extends State<AiPrioritizationSection> {
  late final _api = widget._injectedApi ?? TaskApiClient();
  _AiLoadStatus _status = _AiLoadStatus.loadingExisting;
  TaskAiResult? _result;
  String? _error;

  @override
  void initState() {
    super.initState();
    _loadExisting();
  }

  Future<void> _loadExisting() async {
    try {
      final result = await _api.getLatestAiResult(widget.taskId);
      if (!mounted) return;
      setState(() {
        _result = result;
        _status = _AiLoadStatus.loaded;
      });
    } catch (_) {
      // No existing result is fine -- just show the "Prioritize" button.
      if (!mounted) return;
      setState(() => _status = _AiLoadStatus.loaded);
    }
  }

  Future<void> _prioritize() async {
    setState(() {
      _status = _AiLoadStatus.running;
      _error = null;
    });
    try {
      final result = await _api.prioritizeTask(widget.taskId);
      if (!mounted) return;
      setState(() {
        _result = result;
        _status = _AiLoadStatus.loaded;
      });
    } on TaskApiException catch (e) {
      if (!mounted) return;
      setState(() {
        _error = e.message;
        _status = _AiLoadStatus.error;
      });
    } catch (_) {
      if (!mounted) return;
      setState(() {
        _error = 'Something went wrong. Please try again.';
        _status = _AiLoadStatus.error;
      });
    }
  }

  @override
  Widget build(BuildContext context) {
    if (_status == _AiLoadStatus.loadingExisting) {
      return const Padding(
        padding: EdgeInsets.symmetric(vertical: 8),
        child: Center(child: CircularProgressIndicator(strokeWidth: 2)),
      );
    }

    final busy = _status == _AiLoadStatus.running;

    return Card(
      child: Padding(
        padding: const EdgeInsets.all(16),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Row(
              children: [
                const Icon(Icons.auto_awesome, size: 20),
                const SizedBox(width: 8),
                Text('AI prioritization', style: Theme.of(context).textTheme.titleMedium),
              ],
            ),
            const SizedBox(height: 12),
            if (_status == _AiLoadStatus.error) ...[
              Text(_error ?? 'Failed to prioritize.',
                  style: TextStyle(color: Theme.of(context).colorScheme.error)),
              const SizedBox(height: 8),
            ],
            if (_result != null) _ResultView(result: _result!),
            if (_result != null) const SizedBox(height: 12),
            OutlinedButton.icon(
              onPressed: busy ? null : _prioritize,
              icon: busy
                  ? const SizedBox(
                      height: 16, width: 16, child: CircularProgressIndicator(strokeWidth: 2))
                  : const Icon(Icons.auto_awesome),
              label: Text(busy
                  ? 'Analyzing…'
                  : _result == null
                      ? 'Prioritize with AI'
                      : 'Re-prioritize with AI'),
            ),
          ],
        ),
      ),
    );
  }
}

class _ResultView extends StatelessWidget {
  const _ResultView({required this.result});

  final TaskAiResult result;

  @override
  Widget build(BuildContext context) {
    final priority = result.priorityScore?.toStringAsFixed(0) ?? '—';
    final confidence = result.confidenceScore != null
        ? '${(result.confidenceScore! * 100).toStringAsFixed(0)}%'
        : '—';

    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Wrap(
          spacing: 8,
          runSpacing: 8,
          children: [
            _StatChip(label: 'Priority', value: priority),
            _StatChip(label: 'Confidence', value: confidence),
            if (result.category != null) _StatChip(label: 'Category', value: result.category!),
            if (result.effortEstimateMinutes != null)
              _StatChip(label: 'Est.', value: '${result.effortEstimateMinutes} min'),
          ],
        ),
        if (result.reasoning != null) ...[
          const SizedBox(height: 10),
          Text(result.reasoning!, style: Theme.of(context).textTheme.bodyMedium),
        ],
      ],
    );
  }
}

class _StatChip extends StatelessWidget {
  const _StatChip({required this.label, required this.value});

  final String label;
  final String value;

  @override
  Widget build(BuildContext context) {
    return Chip(
      label: Text('$label: $value'),
      visualDensity: VisualDensity.compact,
    );
  }
}
