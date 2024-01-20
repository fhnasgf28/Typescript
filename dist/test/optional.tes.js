"use strict";
describe('Optional Parameter', function () {
    it('should support null and undifined', function () {
        function sayHello(name) {
            if (name) {
                console.info(`Hello ${name}`);
            }
            else {
                console.info('Hello World');
            }
        }
        sayHello('farhan');
        const name = undefined;
        sayHello(name);
        sayHello(null);
    });
});
