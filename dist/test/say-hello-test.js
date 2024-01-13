import { sayHello } from "../src/say-hello";
describe('sayHello', function () {
    it('should return hello farhan', function () {
        expect(sayHello('farhan')).toBe('Hello farhan');
    });
});
