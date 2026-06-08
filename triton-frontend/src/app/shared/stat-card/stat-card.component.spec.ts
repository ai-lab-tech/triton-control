import { TestBed } from "@angular/core/testing";
import { StatCardComponent } from "./stat-card.component";

describe("StatCardComponent", () => {
  beforeEach(async () => {
    await TestBed.configureTestingModule({
      imports: [StatCardComponent],
    }).compileComponents();
  });

  it("CreateComponent_TestBedInitialized_CreatesComponentInstance", () => {
    // Arrange
    const fixture = TestBed.createComponent(StatCardComponent);

    // Act
    const component = fixture.componentInstance;

    // Assert
    expect(component).toBeTruthy();
  });

  it("Inputs_DefaultStateInitialized_HasExpectedDefaultValues", () => {
    // Arrange
    const fixture = TestBed.createComponent(StatCardComponent);
    fixture.componentRef.setInput("icon", "home");
    fixture.componentRef.setInput("label", "Test");
    fixture.componentRef.setInput("value", "42");
    const component = fixture.componentInstance;

    // Act
    const icon = component.icon();
    const label = component.label();
    const value = component.value();
    const tone = component.tone();

    // Assert
    expect(icon).toBe("home");
    expect(label).toBe("Test");
    expect(value).toBe("42");
    expect(tone).toBe("teal");
  });
});
