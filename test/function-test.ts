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

    it('should support default value', function () {
        function sayHello(name: string = "farhan") : string {
            return `Hello ${name}`
        }

        expect(sayHello()).toBe("Hello farhan")
        expect(sayHello("farhan")).toBe("Hello farhan")
    })

    // function overloading
    it('sthould support function overloading', function () {
        function callMe(value: number) : number;
        function callMe(value: string) : string;
        function callMe(value: any) : any {
            if(typeof value === 'string') {
                return value.toUpperCase();
            } else if (typeof value === 'number') {
                return value * 30;
            }
        }

        expect(callMe(10)).toBe(300)
        expect(callMe("farhan")).toBe("FARHAN")
    })
})