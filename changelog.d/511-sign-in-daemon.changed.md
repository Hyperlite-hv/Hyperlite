- Signing in is done entirely by `hyperlite-daemon` (#511). It records the last sign-in, and for single sign-on it builds
  the redirection to the identity provider, checks the provider's answer and creates or updates the account. The API
  only handles the browser's redirections and cookies.
