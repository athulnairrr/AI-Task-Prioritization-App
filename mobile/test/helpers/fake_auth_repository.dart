import 'package:mobile/src/auth/auth_repository.dart';
import 'package:supabase_flutter/supabase_flutter.dart';

/// A fixed-token stand-in for [AuthRepository] -- lets API-client-backed
/// widget tests build authenticated request headers without a real
/// Supabase session. Constructs its own standalone [SupabaseClient]
/// (never touching the `Supabase.initialize()` global singleton), so
/// these tests don't need `setUpAll` to initialize Supabase at all.
class FakeAuthRepository extends AuthRepository {
  // `autoRefreshToken: false` matters here -- otherwise GoTrueClient starts
  // a real periodic Timer that trips `testWidgets`' "pending timer after
  // dispose" assertion, since nothing in these tests ever calls dispose().
  FakeAuthRepository({this.token = 'fake-access-token'})
      : super(
          client: SupabaseClient(
            'https://test-project.supabase.co',
            'test-anon-key',
            authOptions: const AuthClientOptions(autoRefreshToken: false),
          ),
        );

  final String? token;

  @override
  String? get accessToken => token;
}
