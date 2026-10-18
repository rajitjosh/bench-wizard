// Checks in with Bench Wizard so it can show "extension connected": right after install, when the
// browser starts, and once a minute for a wizard started later.
importScripts('wizard.js');

function schedule() {
  chrome.alarms.create('hello', { periodInMinutes: 1 });
  sayHello();
}

chrome.runtime.onInstalled.addListener(schedule);
chrome.runtime.onStartup.addListener(schedule);
chrome.alarms.onAlarm.addListener((alarm) => {
  if (alarm.name === 'hello') sayHello();
});
