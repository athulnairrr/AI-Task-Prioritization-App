import 'package:flutter/material.dart';
import 'package:supabase_flutter/supabase_flutter.dart';

import '../../design/tokens.dart';
import '../auth_repository.dart';

/// Sign in / sign up / forgot password, using the existing Supabase Auth
/// SDK directly (no FastAPI involvement -- see /docs/architecture.md
/// "Auth architecture"). Polished but deliberately simple: one screen,
/// two modes, no custom validators beyond what Supabase itself requires.
class SignInScreen extends StatefulWidget {
  const SignInScreen({super.key, AuthRepository? authRepository}) : _injectedAuthRepository = authRepository;

  /// Injectable for tests (a fake subclass) -- defaults to a real
  /// [AuthRepository] wrapping the live Supabase client.
  final AuthRepository? _injectedAuthRepository;

  @override
  State<SignInScreen> createState() => _SignInScreenState();
}

class _SignInScreenState extends State<SignInScreen> {
  final _formKey = GlobalKey<FormState>();
  final _emailController = TextEditingController();
  final _passwordController = TextEditingController();
  late final _authRepository = widget._injectedAuthRepository ?? AuthRepository();

  bool _isSignUp = false;
  bool _submitting = false;
  String? _error;
  String? _info;

  @override
  void dispose() {
    _emailController.dispose();
    _passwordController.dispose();
    super.dispose();
  }

  Future<void> _submit() async {
    if (!_formKey.currentState!.validate()) return;
    setState(() {
      _submitting = true;
      _error = null;
      _info = null;
    });
    final email = _emailController.text.trim();
    final password = _passwordController.text;
    try {
      if (_isSignUp) {
        await _authRepository.signUpWithEmail(email: email, password: password);
        setState(() => _info = 'Check your inbox to confirm your account, then sign in.');
      } else {
        await _authRepository.signInWithEmail(email: email, password: password);
      }
    } on AuthException catch (e) {
      setState(() => _error = e.message);
    } catch (_) {
      setState(() => _error = 'Something went wrong. Please try again.');
    } finally {
      if (mounted) setState(() => _submitting = false);
    }
  }

  Future<void> _forgotPassword() async {
    final email = _emailController.text.trim();
    if (email.isEmpty) {
      setState(() => _error = 'Enter your email above first.');
      return;
    }
    try {
      await _authRepository.sendPasswordResetEmail(email);
      setState(() {
        _error = null;
        _info = 'Password reset email sent -- follow the link to set a new password.';
      });
    } on AuthException catch (e) {
      setState(() => _error = e.message);
    }
  }

  @override
  Widget build(BuildContext context) {
    final scheme = Theme.of(context).colorScheme;

    return Scaffold(
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
                    Container(
                      width: 64,
                      height: 64,
                      alignment: Alignment.center,
                      decoration: BoxDecoration(color: scheme.primaryContainer, shape: BoxShape.circle),
                      child: Icon(Icons.auto_awesome_rounded, color: scheme.primary, size: 30),
                    ),
                    const SizedBox(height: Spacing.lg),
                    Text(
                      _isSignUp ? 'Create your account' : 'Welcome back',
                      style: Theme.of(context).textTheme.headlineSmall,
                      textAlign: TextAlign.center,
                    ),
                    const SizedBox(height: Spacing.xs),
                    Text(
                      _isSignUp
                          ? 'Start turning your work into an optimized schedule.'
                          : 'Sign in to see your plan for today.',
                      style: Theme.of(context).textTheme.bodyMedium?.copyWith(color: scheme.onSurfaceVariant),
                      textAlign: TextAlign.center,
                    ),
                    const SizedBox(height: Spacing.xl),
                    if (_error != null) ...[
                      _MessageBanner(text: _error!, color: scheme.error),
                      const SizedBox(height: Spacing.md),
                    ],
                    if (_info != null) ...[
                      _MessageBanner(text: _info!, color: scheme.primary),
                      const SizedBox(height: Spacing.md),
                    ],
                    TextFormField(
                      controller: _emailController,
                      decoration: const InputDecoration(labelText: 'Email'),
                      keyboardType: TextInputType.emailAddress,
                      textInputAction: TextInputAction.next,
                      validator: (v) => (v == null || !v.contains('@')) ? 'Enter a valid email' : null,
                    ),
                    const SizedBox(height: Spacing.md),
                    TextFormField(
                      controller: _passwordController,
                      decoration: const InputDecoration(labelText: 'Password'),
                      obscureText: true,
                      textInputAction: TextInputAction.done,
                      onFieldSubmitted: (_) => _submit(),
                      validator: (v) => (v == null || v.length < 6) ? 'At least 6 characters' : null,
                    ),
                    const SizedBox(height: Spacing.xl),
                    FilledButton(
                      onPressed: _submitting ? null : _submit,
                      child: _submitting
                          ? const SizedBox(
                              height: 18, width: 18, child: CircularProgressIndicator(strokeWidth: 2))
                          : Text(_isSignUp ? 'Sign up' : 'Sign in'),
                    ),
                    const SizedBox(height: Spacing.sm),
                    TextButton(
                      onPressed: _submitting
                          ? null
                          : () => setState(() {
                                _isSignUp = !_isSignUp;
                                _error = null;
                                _info = null;
                              }),
                      child: Text(_isSignUp
                          ? 'Already have an account? Sign in'
                          : "Don't have an account? Sign up"),
                    ),
                    if (!_isSignUp)
                      TextButton(
                        onPressed: _submitting ? null : _forgotPassword,
                        child: const Text('Forgot password?'),
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

class _MessageBanner extends StatelessWidget {
  const _MessageBanner({required this.text, required this.color});

  final String text;
  final Color color;

  @override
  Widget build(BuildContext context) {
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: Spacing.md, vertical: Spacing.sm),
      decoration: BoxDecoration(color: color.withValues(alpha: 0.1), borderRadius: BorderRadius.circular(10)),
      child: Text(text, style: TextStyle(color: color, fontSize: 13), textAlign: TextAlign.center),
    );
  }
}
