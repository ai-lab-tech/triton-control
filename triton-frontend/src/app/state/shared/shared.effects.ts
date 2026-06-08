import { Injectable, inject } from "@angular/core";
import { MatSnackBar } from "@angular/material/snack-bar";
import { Router } from "@angular/router";
import { Actions, createEffect, ofType } from "@ngrx/effects";
import { tap } from "rxjs/operators";
import {
  displayFailure,
  displayInfo,
  displaySuccess,
  displayWarning,
  routing,
  routingArray,
} from "./shared.actions";

@Injectable()
export class SharedEffects {
  private readonly actions$ = inject(Actions);
  private readonly snackBar = inject(MatSnackBar);
  private readonly router = inject(Router);

  readonly displaySuccess$ = createEffect(
    () =>
      this.actions$.pipe(
        ofType(displaySuccess),
        tap((action) => {
          this.snackBar.open(action.message, action.title || "Success", { duration: 3500 });
        }),
      ),
    { dispatch: false },
  );

  readonly displayInfo$ = createEffect(
    () =>
      this.actions$.pipe(
        ofType(displayInfo),
        tap((action) => {
          this.snackBar.open(action.message, action.title || "Info", { duration: 3500 });
        }),
      ),
    { dispatch: false },
  );

  readonly displayWarning$ = createEffect(
    () =>
      this.actions$.pipe(
        ofType(displayWarning),
        tap((action) => {
          this.snackBar.open(action.message, action.title || "Warning", { duration: 4500 });
        }),
      ),
    { dispatch: false },
  );

  readonly displayFailure$ = createEffect(
    () =>
      this.actions$.pipe(
        ofType(displayFailure),
        tap((action) => {
          this.snackBar.open(action.message, action.title || "Error", { duration: 5000 });
        }),
      ),
    { dispatch: false },
  );

  readonly route$ = createEffect(
    () =>
      this.actions$.pipe(
        ofType(routing),
        tap((action) => {
          void this.router.navigate([action.route]);
        }),
      ),
    { dispatch: false },
  );

  readonly routeArray$ = createEffect(
    () =>
      this.actions$.pipe(
        ofType(routingArray),
        tap((action) => {
          void this.router.navigate(action.routes || []);
        }),
      ),
    { dispatch: false },
  );
}
