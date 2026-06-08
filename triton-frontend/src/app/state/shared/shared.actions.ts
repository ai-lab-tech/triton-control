import { createAction, props } from "@ngrx/store";

export const displaySuccess = createAction(
  "[UI Message] Display Success",
  props<{ title?: string; message: string }>(),
);

export const displayInfo = createAction(
  "[UI Message] Display Info",
  props<{ title?: string; message: string }>(),
);

export const displayWarning = createAction(
  "[UI Message] Display Warning",
  props<{ title?: string; message: string }>(),
);

export const displayFailure = createAction(
  "[UI Message] Display Failure",
  props<{ title?: string; message: string }>(),
);

export const routing = createAction("[Router] Route", props<{ route: string }>());

export const routingArray = createAction("[Router] Route Array", props<{ routes: string[] }>());
