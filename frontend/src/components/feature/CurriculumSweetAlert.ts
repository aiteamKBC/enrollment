import Swal, { type SweetAlertIcon } from 'sweetalert2';
import 'sweetalert2/dist/sweetalert2.min.css';

export type CurriculumAlertOptions = {
  title: string;
  text?: string;
  icon?: SweetAlertIcon;
  timer?: number;
  confirmButtonText?: string;
  denyButtonText?: string;
  onDeny?: () => void | Promise<void>;
};

type CurriculumLoadingOptions = {
  title: string;
  text?: string;
};

type CurriculumConfirmOptions = {
  title: string;
  text?: string;
  icon?: SweetAlertIcon;
  confirmButtonText: string;
  cancelButtonText?: string;
  successTitle?: string;
  successText?: string;
  onConfirm: () => void | Promise<void>;
  /**
   * Optional third choice, for the case where cancelling and confirming are not
   * the only two sensible answers — "discard / keep my work / go back". Omit both
   * fields and the dialog keeps its two buttons.
   */
  denyButtonText?: string;
  onDeny?: () => void | Promise<void>;
  /**
   * Which of the two non-cancel answers is the dangerous one. 'safe' — the
   * default — is the third choice that keeps the reader's work, and is styled
   * green against the confirm. 'danger' is the reverse: the third choice throws
   * something away that the confirm would have kept, and must not be the green
   * button sitting next to it.
   */
  denyButtonTone?: 'safe' | 'danger';
  /**
   * What to say once the dialog has closed on a completed deny — `successTitle`
   * for the third answer. A function rather than a string because `onDeny` runs
   * while this dialog is still up: nothing it does can raise a second dialog of
   * its own without replacing the one it is standing in, and what it managed to
   * do is only known after it has run.
   */
  denySuccess?: () => CurriculumAlertOptions | null | undefined;
};

const loadingPopupClass = 'kbc-standard-swal-loading';

export function showCurriculumAlert({
  title,
  text,
  icon = 'success',
  timer,
  confirmButtonText = 'OK',
  denyButtonText,
  onDeny,
}: CurriculumAlertOptions) {
  return Swal.fire({
    title,
    text,
    icon,
    width: 512,
    showConfirmButton: !timer,
    showDenyButton: Boolean(denyButtonText),
    showLoaderOnDeny: Boolean(onDeny),
    denyButtonText,
    allowOutsideClick: () => !Swal.isLoading(),
    allowEscapeKey: () => !Swal.isLoading(),
    confirmButtonText,
    timer,
    timerProgressBar: Boolean(timer),
    buttonsStyling: false,
    customClass: {
      popup: 'kbc-standard-swal-popup',
      title: 'kbc-standard-swal-title',
      htmlContainer: 'kbc-standard-swal-text',
      actions: 'kbc-standard-swal-actions',
      confirmButton: 'kbc-standard-swal-confirm',
      denyButton: 'kbc-standard-swal-deny',
      timerProgressBar: 'kbc-standard-swal-progress',
      loader: 'kbc-standard-swal-loader',
    },
    preDeny: async () => {
      try {
        await onDeny?.();
        return true;
      } catch (err) {
        Swal.showValidationMessage(err instanceof Error ? err.message : 'Unable to download the file.');
        return false;
      }
    },
  });
}

export function showCurriculumLoading({ title, text }: CurriculumLoadingOptions) {
  return Swal.fire({
    title,
    text,
    icon: 'info',
    width: 512,
    showConfirmButton: false,
    allowOutsideClick: false,
    allowEscapeKey: false,
    buttonsStyling: false,
    didOpen: () => Swal.showLoading(),
    customClass: {
      popup: `kbc-standard-swal-popup ${loadingPopupClass}`,
      title: 'kbc-standard-swal-title',
      htmlContainer: 'kbc-standard-swal-text',
      loader: 'kbc-standard-swal-loader',
    },
  });
}

export async function showCurriculumConfirm({
  title,
  text,
  icon = 'warning',
  confirmButtonText,
  cancelButtonText = 'Cancel',
  successTitle,
  successText,
  onConfirm,
  denyButtonText,
  onDeny,
  denyButtonTone = 'safe',
  denySuccess,
}: CurriculumConfirmOptions) {
  const result = await Swal.fire({
    title,
    text,
    icon,
    width: 512,
    showCancelButton: true,
    showLoaderOnConfirm: true,
    showDenyButton: Boolean(denyButtonText),
    showLoaderOnDeny: true,
    denyButtonText,
    reverseButtons: true,
    focusCancel: true,
    confirmButtonText,
    cancelButtonText,
    buttonsStyling: false,
    allowOutsideClick: () => !Swal.isLoading(),
    allowEscapeKey: () => !Swal.isLoading(),
    customClass: {
      popup: 'kbc-standard-swal-popup',
      title: 'kbc-standard-swal-title',
      htmlContainer: 'kbc-standard-swal-text',
      actions: 'kbc-standard-swal-actions',
      confirmButton: 'kbc-standard-swal-confirm',
      denyButton: `kbc-standard-swal-deny${denyButtonTone === 'danger' ? ' kbc-standard-swal-deny-danger' : ''}`,
      cancelButton: 'kbc-standard-swal-cancel',
      loader: 'kbc-standard-swal-loader',
      validationMessage: 'kbc-standard-swal-validation',
    },
    preConfirm: async () => {
      try {
        await onConfirm();
        return true;
      } catch (err) {
        Swal.showValidationMessage(err instanceof Error ? err.message : 'Unable to complete this action.');
        return false;
      }
    },
    preDeny: async () => {
      try {
        await onDeny?.();
        return true;
      } catch (err) {
        Swal.showValidationMessage(err instanceof Error ? err.message : 'Unable to complete this action.');
        return false;
      }
    },
  });

  if (result.isConfirmed && successTitle) {
    await showCurriculumAlert({
      title: successTitle,
      text: successText,
      icon: 'success',
      timer: 1800,
      confirmButtonText: 'Done',
    });
  }

  // `isDenied` covers a deny whose `preDeny` came back clean; one that threw kept
  // the dialog open and never resolved, so there is nothing to report here.
  if (result.isDenied) {
    const followUp = denySuccess?.();
    if (followUp) await showCurriculumAlert({ icon: 'success', confirmButtonText: 'Done', ...followUp });
  }

  return result.isConfirmed;
}

export function closeCurriculumLoading() {
  const popup = Swal.getPopup();
  if (popup?.classList.contains(loadingPopupClass)) Swal.close();
}
