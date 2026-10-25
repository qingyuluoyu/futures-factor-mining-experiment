let popupAllowed = false

export function canShowNotificationPopup(): boolean {
  return popupAllowed
}

export function setNotificationPopupAllowed(allowed: boolean): void {
  popupAllowed = allowed
}
