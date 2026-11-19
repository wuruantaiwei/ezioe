// Please see documentation at https://docs.microsoft.com/aspnet/core/client-side/bundling-and-minification
// for details on configuring this project to bundle and minify static web assets.
// Write your Javascript code.

goOauth =
{
    IsNullOrEmpty: function (str) { return (str == null || str == undefined || str.length == 0); },

    getFingerDeviceId: async function () {
        var deviceIdFinger = localStorage.getItem("deviceIdFinger");
        if (!this.IsNullOrEmpty(deviceIdFinger)) return deviceIdFinger;
        if (typeof FingerprintJS !== 'undefined') {
            try {
                const fp = await FingerprintJS.load();
                const result = await fp.get();
                console.log("Mã vân tay thiết bị:", result.visitorId);
                localStorage.setItem("deviceIdFinger", result.visitorId);
                return result.visitorId;
            } catch (e) {
                console.error("Lỗi Fingerprint:", e);
                return this.getDeviceId();
            }
        } else {
            return this.getDeviceId();
        }
    },

    getDeviceId: function () {
        var deviceId = localStorage.getItem("deviceId");
        if (!this.IsNullOrEmpty(deviceId)) return deviceId;
        var chars = '0123456789abcdef'.split('');

        var uuid = [], rnd = Math.random, r;
        uuid[8] = uuid[13] = uuid[18] = uuid[23] = '-';
        uuid[14] = '4'; // version 4

        for (var i = 0; i < 36; i++) {
            if (!uuid[i]) {
                r = 0 | rnd() * 16;

                uuid[i] = chars[(i == 19) ? (r & 0x3) | 0x8 : r & 0xf];
            }
        }
        deviceId = uuid.join('');
        localStorage.setItem("deviceId", deviceId);
        return deviceId;
    },

    Init: function () {
        //this.getFingerDeviceId().then(deviceId => {
        //    var setinfo = {};
        //    setinfo.deviceId = deviceId; // Lúc này deviceId đã là giá trị thật

        //    var apiUrl = $("#rootPath").val() + '/api/client/setinfo';

        //    fetch(apiUrl, {
        //        method: 'POST',
        //        headers: {
        //            'Content-Type': 'application/json'
        //        },
        //        body: JSON.stringify(setinfo)
        //    })
        //        .then(response => response.text())
        //        .then(dataResponse => {
        //            console.log("Kết quả từ API:", dataResponse);
        //        })
        //        .catch(error => {
        //            console.error('Error Fetch:', error);
        //        });
        //});
    },

    getUserData: function () {
        var struserData = window.localStorage.getItem("userData");
        //var struserData = window.sessionStorage.getItem("userData");
        var userData = {};
        if (this.IsNullOrEmpty(struserData)) {
            this.SaveUserData(userData);
            return userData;
        }
        //return JSON.parse(struserData);
        return JSON.parse(atob(struserData));
    },
    SaveUserData: function (userData) {
        var strData = JSON.stringify(userData).toString();
        console.log(strData);
        var base64 = btoa(strData);
        window.localStorage.setItem("userData", base64);
        //window.sessionStorage.setItem("userData", base64);
    },
    getClient: function (client_id) {
        if (this.IsNullOrEmpty(client_id)) { return null; }
        var userData = this.getUserData();
        if (userData[client_id] == null || userData[client_id] == undefined) {
            userData[client_id] = {}
            this.SaveUserData(userData);
        }
        return userData[client_id];
    },
    getRefreshToken: function (client_id, username) {
        var clientData = this.getClient(client_id);
        var rt = clientData[username];
        if (!this.IsNullOrEmpty(rt)) { return rt; }
        return sessionStorage.getItem(username);
    },
    setClient: function (client_id, clientData) {
        if (this.IsNullOrEmpty(client_id)) { return; }
        var userData = this.getUserData();
        userData[client_id] = clientData;
        this.SaveUserData(userData);
    },
    SignIn: function (client_id, username, account_type, refresh_token, savetoken) {
        if (this.IsNullOrEmpty(client_id)) { return; }
        if (savetoken == null || savetoken == undefined) { savetoken = 0; }

        if (account_type == '1') {
            var clientData = this.getClient(client_id);
            clientData[username] = '';
            if (savetoken == 1) {
                console.log('save refresh_token');
                clientData[username] = refresh_token;
                sessionStorage.setItem('remember_ac', savetoken);
                this.setClient(client_id, clientData);
            } else {
                sessionStorage.removeItem('remember_ac');
                if (clientData[username]) {
                    this.DeleteUser(client_id, clientData[username]);
                    clientData[username] = '';
                }
            }
        }
        sessionStorage.setItem('account_type', account_type);
        sessionStorage.setItem(username, refresh_token);
    },
    SignOut: function (client_id, username) {
        if (this.IsNullOrEmpty(client_id)) { return; }
        if (!this.IsNullOrEmpty(username)) {
            var clientData = this.getClient(client_id);
            //delete clientData[username];
            if (clientData[username]) {
                //clientData[username] = '';
                let remember_ac = sessionStorage.getItem('remember_ac');
                if (!remember_ac || Number(remember_ac) == 0) delete clientData[username];
                this.setClient(client_id, clientData);
            }
        }
        sessionStorage.removeItem(username);
        //sessionStorage.removeItem(client_id + "_access_token");
        //sessionStorage.removeItem(client_id + "_refresh_token");
        //document.cookie = "_s7501=; expires=Thu, 01 Jan 1970 00:00:00 UTC; path=/;"; //UrlNapgoBack
    },
    SignOutAll: function (client_id) {
        if (this.IsNullOrEmpty(client_id)) { return; }
        this.setClient(client_id, {});
        //document.cookie = "_s7501=; expires=Thu, 01 Jan 1970 00:00:00 UTC; path=/;"; //UrlNapgoBack
    },
    DeleteUser: function (client_id, username) {
        if (this.IsNullOrEmpty(client_id)) { return; }
        if (!this.IsNullOrEmpty(username)) {
            var clientData = this.getClient(client_id);
            delete clientData[username];
            this.setClient(client_id, clientData);
            sessionStorage.removeItem(username);
        }
        //sessionStorage.removeItem(client_id + "_access_token");
        //sessionStorage.removeItem(client_id + "_refresh_token");
        //document.cookie = "_s7501=; expires=Thu, 01 Jan 1970 00:00:00 UTC; path=/;"; //UrlNapgoBack
    },
    DeleteAll: function (client_id) {
        if (this.IsNullOrEmpty(client_id)) { return; }
        this.setClient(client_id, {});
        //sessionStorage.removeItem(client_id + "_access_token");
        //sessionStorage.removeItem(client_id + "_refresh_token");
        //document.cookie = "_s7501=; expires=Thu, 01 Jan 1970 00:00:00 UTC; path=/;"; //UrlNapgoBack
    }
};






