//  function typescript
describe("should support function", function () {
    it("should support in typescript", function () {
        function sayHello(name: string) : string {
            return `Hello ${name}`
        }

        expect(sayHello("farhan")).toBe("Hello farhan")

        function printHello(name: string) :void {
            console.info(`Hello ${name}`)
        }

        printHello("farhan");
    })
})