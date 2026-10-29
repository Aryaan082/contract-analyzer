// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

contract ID1 {
    address public ID2;
    mapping(address => uint256) public ID3;

    constructor(address ID4) {
        ID2 = ID4;
    }

    function ID5(address ID6, uint256 ID7, uint8 ID8, bytes32 ID9, bytes32 ID10) external {
        bytes32 ID11 = keccak256(abi.encodePacked(ID6, ID7));
        address ID12 = ecrecover(ID11, ID8, ID9, ID10);
        require(ID12 == ID2, "bad signature");
        ID3[ID6] += ID7;
    }
}
