import 'package:flutter/material.dart';
import 'package:supabase_flutter/supabase_flutter.dart';

import '../../design/tokens.dart';
import '../auth_repository.dart';

/// Shown when Supabase Auth reports an `AuthChangeEvent.passwordRecovery`
/// (the user opened their "reset password" email link and it was
/// exchanged for a recovery session) -- see AuthGate. Completing this
/// screen calls `auth.updateUser` with the new password.
///
/// Known limitation: this app has no deep-link registration yet (mobile
/// Calendar OAuth has the same gap, see ADR-015/docs/progress.md), so the
/// recovery link opens in an external browser rather than returning here
/// automatically. The screen and the event wiring are both correct and
/// ready for when deep linking is added; today, a user who completes the
/// email flow in the browser can simply sign in with the emailed
/// temporary state or request a new link and use "Forgot password" again.
class ResetPasswordScreen extends StatefulWidget {
  const ResetPasswordScreen({super.key, required this.onDone});

  final VoidCallback onDone;

  @override
  State<ResetPasswordScreen> createState() => _ResetPasswordScreenState();
}

class _ResetPasswordScreenState extends State<ResetPasswordScreen> {
  final _formKey = GlobalKey<FormState>();
  final _passwordController = TextEditingController();
  final _confirmController = TextEditingController();
  final _authRepository = AuthRepository();

  bool _submitting = false;
  String? _error;

  @override
  void dispose() {
    _passwordController.dispose();
    _confirmController.dispose();
    super.dispose();
  }

  Future<void> _submit() async {
    if (!_formKey.currentState!.validate()) return;
    setState(() {
      _submitting = true;
      _error = null;
    });
    try {
      await _authRepository.updatePassword(_passwordController.text);
      if (mounted) widget.onDone();
    } on AuthException catch (e) {
      setState(() => _error = e.message);
    } catch (_) {
      setState(() => _error = 'Something went wrong. Please try again.');
    } finally {
      if (mounted) setState(() => _submitting = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: const Text('Reset password')),
      body: SafeArea(
        child: Center(
          child: SingleChildScrollView(
            padding: const EdgeInsets.all(Spacing.xl),
            child: ConstrainedBox(
              constraints: const BoxConstraints(maxWidth: 420),
              child: Form(
                key: _formKey,
                child: Column(
                  mainAxisSize: MainAxisSize.min,
                  crossAxisAlignment: CrossAxisAlignment.stretch,
                  children: [
                    Text('Choose a new password', style: Theme.of(context).textTheme.titleLarge),
                    const SizedBox(height: Spacing.xs),
                    Text(
                      'This replaces your current password.',
                      style: Theme.of(context)
                          .textTheme
                          .bodyMedium
                          ?.copyWith(color: Theme.of(context).colorScheme.onSurfaceVariant),
                    ),
                    const SizedBox(height: Spacing.xl),
                    if (_error != null) ...[
                      Text(_error!, style: TextStyle(color: Theme.of(context).colorScheme.error)),
                      const SizedBox(height: Spacing.md),
                    ],
                    TextFormField(
                      controller: _passwordController,
                      decoration: const InputDecoration(labelText: 'New password'),
                      obscureText: true,
                      textInputAction: TextInputAction.next,
                      validator: (v) => (v == null || v.length < 6) ? 'At least 6 characters' : null,
                    ),
                    const SizedBox(height: Spacing.md),
                    TextFormField(
                      controller: _confirmController,
                      decoration: const InputDecoration(labelText: 'Confirm password'),
                      obscureText: true,
                      textInputAction: TextInputAction.done,
                      onFieldSubmitted: (_) => _submit(),
                      validator: (v) =>
                          v != _passwordController.text ? 'Passwords do not match' : null,
                    ),
                    const SizedBox(height: Spacing.xl),
                    FilledButton(
                      onPressed: _submitting ? null : _submit,
                      child: _submitting
                          ? const SizedBox(
                              height: 18, width: 18, child: CircularProgressIndicator(strokeWidth: 2))
                          : const Text('Save new password'),
                    ),
                  ],
                ),
              ),
            ),
          ),
        ),
      ),
    );
  }
}
