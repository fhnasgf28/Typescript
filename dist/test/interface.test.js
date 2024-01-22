describe('interface', function () {
    it('should support in typescript', function () {
        const seller = {
            id: 1,
            name: ' Toko ABC',
            nib: "121233",
        };
        seller.name = 'Toko Farhan';
        console.log(seller);
    });
    it('should support function interface', function () {
        const add = (value1, value2) => {
            return value1 + value2;
        };
        expect(add(2, 2));
    });
    it('should support indexable interface', function () {
        const names = ["farhan", "assegaf", "Han"];
        console.info(names);
    });
    it('should support indexable interface for non numbers', function () {
        const dictionary = {
            "name": "Farhan",
            "address": "Cirebon",
        };
        expect(dictionary["name"]).toBe("Farhan");
        expect(dictionary["address"]).toBe("Cirebon");
    });
    it("should support extends interface", function () {
        const employee = {
            id: "1",
            name: "Farhan",
            division: "IT"
        };
        console.info(employee);
        const manager = {
            id: "2",
            name: "FarhanA",
            division: "IT",
            numberOfEmployees: 10
        };
        console.info(manager);
    });
    it("should support function in interface", function () {
        const person = {
            name: "John",
            sayHello: function (name) {
                return `Hello ${name}, my name is ${this.name}`;
            }
        };
        console.info(person.sayHello("Farhan"));
    });
    it("should support intersection types", function () {
        const domain = {
            name: "Farhan",
            id: "1",
        };
        console.info(domain);
    });
    // assertion type
    it("should support assertion type", function () {
        const person = {
            name: "Farhan",
            age: 28
        };
        const person2 = person;
    });
});
export {};
