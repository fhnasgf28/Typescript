
import { Person } from "../src/person";
import { Employee, Manager } from "../src/employee";

describe ('interface', function() {
    it('should support in typescript', function() {
        const seller = {
            id:1,
            name:' Toko ABC',
            nib: "121233",

        };
        seller.name= 'Toko Farhan'
        console.log(seller);
    });
    it('should support function interface', function() {
        interface AddFunction {
            (value1: number, value2: number): number;
        }

        const add: AddFunction = (value1: number, value2: number) : number => {
            return value1 + value2;
        };

        expect(add(2,2))
    })

    it('should support indexable interface', function() {
        interface StringArray {
            [index: number]: string
        }

        const names: StringArray = ["farhan","assegaf","Han"];
        console.info(names);
    });

    it('should support indexable interface for non numbers', function() {
        interface StringDictionary {
            [key: string]: string
        }

        const dictionary: StringDictionary = {
            "name" : "Farhan",
            "address" : "Cirebon",
        };

        expect(dictionary["name"]).toBe("Farhan");
        expect(dictionary["address"]).toBe("Cirebon");
    })

    it("should support extends interface", function () {
    const employee: Employee = {
        id: "1",
        name: "Farhan",
        division: "IT"
    }
    console.info(employee);

    const manager: Manager ={
        id: "2",
        name: "FarhanA",
        division: "IT",
        numberOfEmployees: 10
    }
    console.info(manager);

    })

    it("should support function in interface", function () {
        interface Person {
            name: string;
            sayHello(name: string): string;
        }

        const person: Person = {
            name: "John",
            sayHello: function (name: string) {
                return `Hello ${name}, my name is ${this.name}`
            }
        }
        console.info(person.sayHello("Farhan"));
    });

    it("should support intersection types", function () {
        interface HasName {
            name: string;
        }
        interface HasId {
            id: string;
        }

        type Domain = HasName & HasId;

        const domain: Domain = {
            name: "Farhan",
            id: "1",
        }

        console.info(domain)
    })

    // assertion type
    it("should support assertion type", function () {
        const person: any = {
            name: "Farhan",
            age: 28
        };

        const person2 : Person = person as Person;
    })

})
