import { loginFeature } from "./login.reducer";

export const {
  selectOidcEnabled: selectLoginOidcEnabled,
  selectNeedsBootstrap: selectLoginNeedsBootstrap,
  selectLocalAdminSetupRequired: selectLoginLocalAdminSetupRequired,
  selectLoading: selectLoginLoading,
  selectError: selectLoginError,
  selectNotice: selectLoginNotice,
  selectRegisterMode: selectLoginRegisterMode,
  selectForgotPasswordAvailable: selectLoginForgotPasswordAvailable,
} = loginFeature;
