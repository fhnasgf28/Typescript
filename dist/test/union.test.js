"use strict";
describe('Union Type', function () {
    it('should support in typescript', function () {
        let sample = "Farhan";
        console.info(sample);
        sample = 100;
        console.info(sample);
        sample = true;
        console.info(sample);
    });
});
it('should support typeof operator', function () {
    function process(value) {
        if (typeof value === 'string') {
            return value.toUpperCase();
        }
        else if (typeof value === 'number') {
            return value + 3;
        }
        else {
            return !value;
        }
    }
});
